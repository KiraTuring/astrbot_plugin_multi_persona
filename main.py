from re import U
from typing import Optional
from astrbot.api.provider import ProviderRequest
from astrbot.api.event import filter, AstrMessageEvent, MessageEventResult, MessageChain
from astrbot.api.star import Context, Star, register
from astrbot.api.provider import LLMResponse
from astrbot.api import logger, AstrBotConfig
from astrbot.core.db import Persona, PersonaFolder
from astrbot.core.conversation_mgr import Conversation
from astrbot.core.provider.func_tool_manager import FunctionTool, FunctionToolManager
from astrbot.core.agent.tool import ToolSet
from astrbot.core.agent.message import (
    Message,
    AssistantMessageSegment,
    UserMessageSegment,
    TextPart,
)
from astrbot.core.message.components import Plain
import json
import asyncio


class MultiPersonaPlugin(Star):
    def __init__(self, context: Context, config: AstrBotConfig):
        super().__init__(context)
        self.config = config
        self.persona_mgr = self.context.persona_manager
        self.conv_mgr = self.context.conversation_manager
        self.tool_mgr = self.context.get_llm_tool_manager()

    async def initialize(self):
        self.pop = self.config['population']
        self.user_name = self.config['user_name']
        self.mode = self.config['mode']
        self.p_list: list[dict] = self.config['persona_list']

        self.active_idx = 0  # 默认选择第一个人格
        active_pid = self.p_list[self.active_idx]['persona_id']
        try:
            self.persona: Persona = await self.persona_mgr.get_persona(active_pid)
        except ValueError as e:
            logger.error(f"{active_pid}人格不存在: {e}")

        self.dialog_prompt = self.config['dialog_prompt']

        logger.info(f"使用的系统提示: {self.dialog_prompt}")

    @filter.command("mulper_info", alias={'mpi', '人格信息'})
    async def mulper_info(self, event: AstrMessageEvent):
        """查看目前的人格设定和对话上下文等信息"""
        msg = f"目前已有{len(self.p_list)}个人格设置:\n"
        for i, persona in enumerate(self.p_list, 1):
            if i == self.active_idx + 1:
                msg += f"人格{i}: {persona['persona_id']} (当前使用)\n"
            else:
                msg += f"人格{i}: {persona['persona_id']}\n"
        yield event.plain_result(msg)

        umo = event.unified_msg_origin
        curr_cid = await self.conv_mgr.get_curr_conversation_id(umo)
        if curr_cid is None:
            # 如果当前没有对话，先创建一个对话
            curr_cid = await self.conv_mgr.new_conversation(umo)
        conversation = await self.conv_mgr.get_conversation(umo, curr_cid)
        assert conversation is not None, "当前对话不能为空"

        yield event.plain_result(f"当前对话上下文包含{len(json.loads(conversation.history))}条消息")

        logger.info(f"当前对话上下文: {json.loads(conversation.history)}")

    @filter.command("mulper_switch", alias={'mps', '人格切换'})
    async def mulper_switch(self, event: AstrMessageEvent, idx: Optional[int] = None):
        """切换目前使用的人格"""
        old_p = self.p_list[self.active_idx]
        if idx is None:
            new_p = await self._switch_persona()
        else:
            if idx < 1 or idx > len(self.p_list):
                yield event.plain_result(f"无效的人格编号，请输入1-{len(self.p_list)}之间的数字")
                return
            self.active_idx = idx - 1
            new_p = self.p_list[self.active_idx]
            self.persona = await self.persona_mgr.get_persona(new_p['persona_id'])

        msg = f"从 {old_p['persona_id']} 人格切换为 {new_p['persona_id']} 人格"
        yield event.plain_result(msg)

    async def _switch_persona(self) -> dict:
        """切换目前使用的人格"""
        self.active_idx += 1
        if self.active_idx >= len(self.p_list):
            self.active_idx -= len(self.p_list)
        elif self.active_idx < 0:
            self.active_idx += len(self.p_list)
        new_p = self.p_list[self.active_idx]
        self.persona = await self.persona_mgr.get_persona(new_p['persona_id'])

        return new_p

    # @filter.on_llm_request()
    # async def add_persona_prompt(self, event: AstrMessageEvent, req: ProviderRequest):  # 请注意有三个参数
    #     logger.info(f"LLM 请求上下文: {req.contexts}")
    #     dialog_prompt = (f'你的身份是[{self.name_list[self.active_idx]}]，保持你的身份设定。'
    #                      '和你对话的人不止一个，请根据身份标签判断你在和谁说话，并保持角色设定，进行合理的回复。'
    #                      f'如果你不确定在和谁说话，可以先询问对方的身份。')
    #     req.system_prompt += dialog_prompt

    # @filter.on_llm_response()
    # async def add_persona_tags(self, event: AstrMessageEvent, resp: LLMResponse):  # 请注意有三个参数
    #     """在消息记录中添加身份标签，方便 LLM 判断和谁在说话"""
    #     umo = event.unified_msg_origin
    #     curr_cid = await self.conv_mgr.get_curr_conversation_id(umo)
    #     assert curr_cid is not None, "当前对话 ID 不能为空"

    #     logger.info(f"LLM 返回结果: {resp.result_chain}")
    #     asyncio.create_task(self.add_persona_tags_async(event, curr_cid))

    # async def add_persona_tags_async(self, event: AstrMessageEvent, curr_cid: str):
    #     """在消息记录中添加身份标签，方便 LLM 判断和谁在说话"""
    #     umo = event.unified_msg_origin
    #     all_prefix = [f"[{self.name_list[idx]}]说: \n\n" for idx in range(len(self.name_list))]
    #     prefix = all_prefix[self.active_idx]
    #     other_prefix = [p for i, p in enumerate(all_prefix) if i != self.active_idx]
    #     logger.info(f"准备添加身份标签，当前身份标签: {prefix}，其他身份标签: {other_prefix}")

    #     for i in range(10):
    #         is_ready = True
    #         await asyncio.sleep(5)
    #         conversation = await self.conv_mgr.get_conversation(umo, curr_cid)
    #         assert conversation is not None, "当前对话不能为空"
    #         context = json.loads(conversation.history)
    #         logger.info(f"尝试添加身份标签，最新对话记录: {context[-1]}")
    #         logger.info(event.is_stopped())
    #         if context == [] or context[-1]['role'] != 'assistant':
    #             logger.warning(f"对话上下文不合法，无法添加身份标签，当前最新对话记录: {context[-1]}，5秒后重试")
    #             is_ready = False
    #         elif context[-1]['content'][0]['text'][:len(prefix)] == prefix:
    #             logger.info(f"已经存在身份标签，无需添加，当前最新对话记录: {context[-1]}")
    #             return
    #         else:
    #             for p in other_prefix:
    #                 if context[-1]['content'][0]['text'][:len(p)] == p:
    #                     logger.info(f"身份标签{p}与当前人格不匹配，5秒后重试，当前最新对话记录: {context[-1]}")
    #                     is_ready = False
    #         if is_ready:
    #             break

    #     if not context[-1]['content'][0]['text'].startswith(prefix):
    #         context[-1]['content'][0]['text'] = prefix + context[-1]['content'][0]['text']
    #         logger.info(f"在消息中添加身份标签后，当前对话上下文: {context}")

    #     await self.conv_mgr.update_conversation(umo, history=context)

    # @filter.event_message_type(filter.EventMessageType.ALL)
    # async def add_user_tag(self, event: AstrMessageEvent):
    #     """添加用户消息的身份标签，方便 LLM 判断和谁在说话"""
    #     user_prefix = f"[{self.user_name}]说: \n\n"
    #     if not event.message_str.startswith(user_prefix):
    #         event.message_str = user_prefix + event.message_str

    def _modify_roles(self, context: list[dict]) -> list[dict]:
        """修改消息记录中的角色，将身份标签不是自己的助手消息改为用户消息"""
        all_prefix = [f"[{self.p_list[idx]['name']}]说: \n\n" for idx in range(len(self.p_list))]
        other_prefix = [p for i, p in enumerate(all_prefix) if i != self.active_idx]

        for j in range(len(context)):
            if context[j]['role'] == 'assistant':
                for p in other_prefix:
                    if context[j]['content'][0]['text'].startswith(p):
                        context[j]['role'] = 'user'
        return context

    async def _llm_request(self, event: AstrMessageEvent, context: list[dict], provider_id: str) -> LLMResponse:
        """发送 llm 请求的公共方法，添加系统提示和工具等"""
        context = self._modify_roles(context)
        if len(context) > 5:
            logger.info(f"修改role后，当前对话上下文: {context[-5:]}")
        else:
            logger.info(f"修改role后，当前对话上下文: {context}")

        if self.persona.tools is not None:
            tools_list: list[FunctionTool] = []
            for t in self.persona.tools:
                func_tool = self.tool_mgr.get_func(t)
                if func_tool is not None:
                    tools_list += [func_tool]
            tools = ToolSet(tools=tools_list)
        else:
            tools = None

        dialog_prompt = self.dialog_prompt.format(
            user_name=self.user_name,
            name=self.p_list[self.active_idx]['name'],
        )
        # logger.info(f"使用的系统提示: {self.persona.system_prompt+dialog_prompt}")

        llm_resp: LLMResponse = await self.context.tool_loop_agent(
            event=event,
            chat_provider_id=provider_id,
            contexts=context,  # type: ignore
            tools=tools,
            system_prompt=self.persona.system_prompt+dialog_prompt,
            max_steps=30,  # Agent 最大执行步骤
            tool_call_timeout=60,  # 工具调用超时时间
        )

        assert llm_resp.result_chain is not None, "LLM 生成结果不能为空"
        assert isinstance(llm_resp.result_chain, MessageChain), "LLM 生成结果类型错误，应该为 MessageChain"

        prefix = f"[{self.p_list[self.active_idx]['name']}]说: \n\n"
        for i, part in enumerate(llm_resp.result_chain.chain):
            if isinstance(part, Plain):
                if not part.text.startswith(prefix):
                    llm_resp.result_chain.chain[i].text = prefix + part.text

        return llm_resp

    @filter.command("mulper_continue", alias={'mpc', '人格继续'})
    async def mulper_continue(self, event: AstrMessageEvent):
        """让llm按当前上下文继续生成"""
        umo = event.unified_msg_origin
        provider_id = await self.context.get_current_chat_provider_id(umo)
        curr_cid = await self.conv_mgr.get_curr_conversation_id(umo)
        if curr_cid is None:
            # 如果当前没有对话，先创建一个对话
            curr_cid = await self.conv_mgr.new_conversation(umo)
        conversation = await self.conv_mgr.get_conversation(umo, curr_cid)
        assert conversation is not None, "当前对话不能为空"

        context = json.loads(conversation.history)
        if len(context) > 20:
            logger.warning(f"当前对话上下文过长，可能会导致生成失败，建议清理对话历史，当前上下文长度: {len(context)}")

        llm_resp = await self._llm_request(event, context, provider_id)

        assistant_msg = AssistantMessageSegment(content=[TextPart(text=llm_resp.completion_text)])
        await self.add_message_single(
            cid=curr_cid,
            message=assistant_msg,
        )

        yield event.chain_result(llm_resp.result_chain.chain)  # type: ignore

        if self.mode == 'switch':
            old_p = self.p_list[self.active_idx]
            new_p = await self._switch_persona()
            msg = f"从 {old_p['persona_id']} 人格自动切换为 {new_p['persona_id']} 人格"
            logger.info(msg)
            # yield event.plain_result(msg)

    @filter.command("mulper_message", alias={'mpm', '人格消息'})
    async def mulper_message(self, event: AstrMessageEvent, message: str):
        """让llm按当前上下文+新消息继续生成"""
        user_prefix = f"[{self.user_name}]说: \n\n"
        user_mgs = UserMessageSegment(content=[TextPart(text=user_prefix + message)])

        umo = event.unified_msg_origin
        provider_id = await self.context.get_current_chat_provider_id(umo)
        curr_cid = await self.conv_mgr.get_curr_conversation_id(umo)
        if curr_cid is None:
            # 如果当前没有对话，先创建一个对话
            curr_cid = await self.conv_mgr.new_conversation(umo)
        conversation = await self.conv_mgr.get_conversation(umo, curr_cid)
        assert conversation is not None, "当前对话不能为空"

        context = json.loads(conversation.history)
        context.append(user_mgs.model_dump())
        if len(context) > 20:
            logger.warning(f"当前对话上下文过长，可能会导致生成失败，建议清理对话历史，当前上下文长度: {len(context)}")

        llm_resp = await self._llm_request(event, context, provider_id)

        assistant_msg = AssistantMessageSegment(content=[TextPart(text=llm_resp.completion_text)])
        await self.conv_mgr.add_message_pair(
            cid=curr_cid,
            user_message=user_mgs,
            assistant_message=assistant_msg,
        )

        yield event.chain_result(llm_resp.result_chain.chain)  # type: ignore

        if self.mode == 'switch':
            old_p = self.p_list[self.active_idx]
            new_p = await self._switch_persona()
            msg = f"从 {old_p['persona_id']} 人格自动切换为 {new_p['persona_id']} 人格"
            logger.info(msg)
            # yield event.plain_result(msg)

    async def terminate(self):
        """可选择实现异步的插件销毁方法，当插件被卸载/停用时会调用。"""

    async def add_message_single(
        self,
        cid: str,
        message: Message | dict,
    ) -> None:
        """Add a user-assistant message pair to the conversation history.

        Args:
            cid (str): Conversation ID
            user_message (UserMessageSegment | dict): OpenAI-format user message object or dict
            assistant_message (AssistantMessageSegment | dict): OpenAI-format assistant message object or dict

        Raises:
            Exception: If the conversation with the given ID is not found
        """
        conv = await self.conv_mgr.db.get_conversation_by_id(cid=cid)
        if not conv:
            raise Exception(f"Conversation with id {cid} not found")
        history = conv.content or []
        if isinstance(message, Message):
            msg_dict = message.model_dump()
        else:
            msg_dict = message
        history.append(msg_dict)
        await self.conv_mgr.db.update_conversation(
            cid=cid,
            content=history,
        )
