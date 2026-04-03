from typing import Optional
from astrbot.api.event import AstrMessageEvent, MessageChain
from astrbot.api import logger, AstrBotConfig
from astrbot.api.star import Context
from astrbot.core.provider.func_tool_manager import FunctionTool, FunctionToolManager
from astrbot.api.provider import LLMResponse
from astrbot.core.agent.tool import ToolSet
from astrbot.core.persona_mgr import PersonaManager
from astrbot.core.conversation_mgr import ConversationManager
from astrbot.core.db import Persona
from astrbot.core.message.components import Plain
from astrbot.core.agent.message import (
    Message,
    AssistantMessageSegment,
    UserMessageSegment,
    TextPart,
)
import asyncio
from . import helper


class Actor:
    def __init__(self,
                 name: str,
                 persona_id: str,
                 persona: Persona,
                 tools: Optional[ToolSet] = None
                 ):
        self.name = name
        self.prefix = f"[{self.name}]说: \n"
        self.persona_id = persona_id
        self.persona = persona
        self.tools = tools

    @classmethod
    async def create_with_context(cls, name: str, persona_id: str, context: Context) -> 'Actor':
        """通过上下文创建 Actor 实例，自动加载人格信息和工具"""
        persona_mgr = context.persona_manager
        tool_mgr = context.get_llm_tool_manager()
        try:
            persona = await persona_mgr.get_persona(persona_id)
        except ValueError as e:
            raise ValueError(f"{persona_id}人格不存在: {e}")
        if persona.tools is not None:
            tools_list: list[FunctionTool] = []
            for t in persona.tools:
                func_tool = tool_mgr.get_func(t)
                if func_tool is not None:
                    tools_list += [func_tool]
            tools = ToolSet(tools=tools_list)
        else:
            tools = None
        return cls(name=name, persona_id=persona_id, persona=persona, tools=tools)

    @classmethod
    async def create_all_with_context(cls, config: AstrBotConfig, context: Context) -> list['Actor']:
        """通过上下文创建所有 Actor 实例，自动加载人格信息和工具"""
        actors = []
        for p in config['persona_list']:
            actor = await cls.create_with_context(name=p['name'], persona_id=p['persona_id'], context=context)
            actors.append(actor)
        return actors

    def get_system_prompt(self, config: dict) -> str:
        """获取系统提示，包含人格信息和对话提示"""
        dialog_prompt = config['dialog_prompt'].format(
            user_name=config['user_name'],
            name=self.name,
        )
        return self.persona.system_prompt + dialog_prompt

    def get_additional_prompt(self, world_state: str = '', extracted_context: str = '') -> str:
        """获取附加提示，包含对话上下文等信息"""
        additional_prompt = ''
        if world_state:
            additional_prompt += f'\n【世界状态】\n{world_state}'
            additional_prompt += '\n------\n'
        if extracted_context:
            additional_prompt += f'\n【对话历史】\n{extracted_context}'
            additional_prompt += '\n------\n'
        additional_prompt += f'\n【你的任务】\n以[{self.name}]的身份回应对话，禁止原样重复之前的句子，包括动作和心理活动。'

        return additional_prompt


async def request_actor_llm(actor: Actor, context: Context, event: AstrMessageEvent,
                            config: dict, extracted_context: str) -> LLMResponse:
    """发送 llm 请求的公共方法，添加系统提示和工具等"""
    umo = event.unified_msg_origin
    provider_id = await context.get_current_chat_provider_id(umo)
    conv_mgr = context.conversation_manager
    cid = await conv_mgr.get_curr_conversation_id(umo)
    if cid is None:
        # 如果当前没有对话，先创建一个对话
        cid = await conv_mgr.new_conversation(umo)

    system_prompt = actor.get_system_prompt(config)
    logger.info(f"使用的系统提示: {system_prompt}")

    world_state = ''
    for ws in config["world_state"]:
        if ws["conversation_id"] == cid:
            world_state = ws["state"]
            break

    # conv_list = await get_conversation_context(conv_mgr, cid)
    additional_prompt = actor.get_additional_prompt(world_state, extracted_context)
    logger.info(f"使用的附加提示: {additional_prompt}")

    llm_resp: LLMResponse = await context.tool_loop_agent(
        event=event,
        chat_provider_id=provider_id,
        # contexts=context,  # type: ignore
        tools=actor.tools,
        system_prompt=system_prompt,
        prompt=additional_prompt,
        max_steps=5,  # Agent 最大执行步骤
        tool_call_timeout=60,  # 工具调用超时时间
    )

    assert llm_resp.result_chain is not None, "LLM 生成结果不能为空"
    assert isinstance(llm_resp.result_chain, MessageChain), "LLM 生成结果类型错误，应该为 MessageChain"

    for i, part in enumerate(llm_resp.result_chain.chain):
        if isinstance(part, Plain):
            if not part.text.startswith(actor.prefix):
                llm_resp.result_chain.chain[i].text = actor.prefix + part.text

    # await add_message_history(conv_mgr, cid, llm_resp)
    await context.send_message(event.unified_msg_origin, llm_resp.result_chain)  # type: ignore
    return llm_resp


async def add_message_history(conv_mgr: ConversationManager, cid: str, llm_resp: LLMResponse):
    """将消息添加到对话历史中"""
    assistant_msg = AssistantMessageSegment(content=[TextPart(text=llm_resp.completion_text)])
    message = assistant_msg,

    conv = await conv_mgr.db.get_conversation_by_id(cid=cid)
    if not conv:
        raise Exception(f"Conversation with id {cid} not found")
    history = conv.content or []
    if isinstance(message, Message):
        msg_dict = message.model_dump()
    else:
        msg_dict = message
    history.append(msg_dict)
    await conv_mgr.db.update_conversation(
        cid=cid,
        content=history,
    )
