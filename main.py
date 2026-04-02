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
from astrbot.core.utils.astrbot_path import get_astrbot_data_path
from astrbot.core.agent.message import (
    Message,
    AssistantMessageSegment,
    UserMessageSegment,
    TextPart,
)
from astrbot.core.message.components import Plain
import json
import asyncio
import os


class MultiPersonaPlugin(Star):
    def __init__(self, context: Context, config: AstrBotConfig):
        super().__init__(context)
        self.config = config
        self.persona_mgr = self.context.persona_manager
        self.conv_mgr = self.context.conversation_manager
        self.tool_mgr = self.context.get_llm_tool_manager()
        self.data_path = get_astrbot_data_path() + f"/plugin_data/{self.name}"
        os.makedirs(self.data_path, exist_ok=True)

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
        self.summary_prompt = self.config['summary_prompt']

        for i, ws in enumerate(self.config["world_state"]):
            conv = await self.conv_mgr.db.get_conversation_by_id(cid=ws['conversation_id'])
            if not conv:
                logger.warning(f"对话 {ws['conversation_id']} 不存在，删除无效的世界状态配置")
                self.config["world_state"].pop(i)
                self.config.save_config()

    @filter.command("mulper_info", alias={'mpi', '人格信息'})
    async def mulper_info(self, event: AstrMessageEvent):
        """查看目前的人格设定和对话上下文等信息"""
        msg = f"目前已有{len(self.p_list)}个人格设置:\n"
        for i, persona in enumerate(self.p_list, 1):
            if i == self.active_idx + 1:
                msg += f"人格{i}: {persona['persona_id']} (当前使用)\n"
            else:
                msg += f"人格{i}: {persona['persona_id']}\n"

        umo = event.unified_msg_origin
        curr_cid = await self.conv_mgr.get_curr_conversation_id(umo)
        if curr_cid is None:
            # 如果当前没有对话，先创建一个对话
            curr_cid = await self.conv_mgr.new_conversation(umo)
        msg += f"当前对话 ID: {curr_cid}\n"

        conversation = await self.conv_mgr.get_conversation(umo, curr_cid)
        assert conversation is not None, "当前对话不能为空"
        msg += f"当前对话上下文包含{len(json.loads(conversation.history))}条消息"
        logger.info(f"当前对话上下文: {json.loads(conversation.history)}")
        yield event.plain_result(msg)

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

    async def _switch_persona(self, idx: Optional[int] = None) -> dict:
        """切换目前使用的人格"""
        if idx is not None:
            self.active_idx = idx - 1
        else:
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

    # def _modify_roles(self, context: list[dict], active_idx: Optional[int] = None) -> list[dict]:
    #     """修改消息记录中的角色，将身份标签不是自己的助手消息改为用户消息"""
    #     all_prefix = [f"[{self.p_list[idx]['name']}]说: \n" for idx in range(len(self.p_list))]
    #     if active_idx is None:
    #         active_idx = self.active_idx
    #     other_prefix = [p for i, p in enumerate(all_prefix) if i != active_idx]

    #     for j in range(len(context)):
    #         if context[j]['role'] == 'assistant':
    #             for p in other_prefix:
    #                 if context[j]['content'][0]['text'].startswith(p):
    #                     context[j]['role'] = 'user'
    #     return context

    def _extract_context(self, context: list[dict]) -> str:
        """从消息记录中提取对话上下文，去掉身份标签等无关信息"""
        all_prefix = [f"[{self.p_list[idx]['name']}]说: \n" for idx in range(len(self.p_list))]
        user_prefix = f"[{self.user_name}]说: \n"
        extracted_context = ''
        for msg in context:
            if msg['role'] == 'assistant':
                for p in all_prefix:
                    if msg['content'][0]['text'].startswith(p):
                        extracted_context += msg['content'][0]['text'] + '\n\n'
                        break
            elif msg['role'] == 'user':
                if msg['content'][0]['text'].startswith(user_prefix):
                    extracted_context += msg['content'][0]['text'] + '\n\n'

        return extracted_context

    async def _llm_request(self, event: AstrMessageEvent, context: list[dict], provider_id: str, cid: str) -> LLMResponse:
        """发送 llm 请求的公共方法，添加系统提示和工具等"""
        # context = context[-self.config['max_context_length']:]  # 截取最近的消息，避免上下文过长导致生成失败

        # context = self._modify_roles(context)
        # if len(context) > 5:
        #     logger.info(f"修改role后，当前对话上下文: {context[-5:]}")
        # else:
        #     logger.info(f"修改role后，当前对话上下文: {context}")

        # TODO: 改成LLM判断是否需要总结更新世界状态，而不是单纯根据消息记录条数判断
        if len(context) > self.config['max_context_length']+self.config['state_update_every']:
            logger.warning(f"对话上下文过长，当前消息记录条数: {len(context)}，将自动更新世界状态并清理对话历史")
            await self._context_summary(event, context, provider_id, cid, save=True)

        extracted_context = self._extract_context(context)

        if self.persona.tools is not None:
            tools_list: list[FunctionTool] = []
            for t in self.persona.tools:
                func_tool = self.tool_mgr.get_func(t)
                if func_tool is not None:
                    tools_list += [func_tool]
            tools = ToolSet(tools=tools_list)
        else:
            tools = None

        name = self.p_list[self.active_idx]['name']
        dialog_prompt = self.dialog_prompt.format(
            user_name=self.user_name,
            name=name,
        )
        logger.info(f"使用的系统提示: {self.persona.system_prompt+dialog_prompt}")

        additional_prompt = ''
        for ws in self.config["world_state"]:
            if ws["conversation_id"] == cid:
                additional_prompt += f'\n【世界状态】\n{ws["state"]}'
                break
        additional_prompt += f'\n【对话历史】\n{extracted_context}'
        additional_prompt += f'\n【你的任务】\n以[{name}]的身份回应对话。'
        logger.info(f"使用的附加提示: {additional_prompt}")

        llm_resp: LLMResponse = await self.context.tool_loop_agent(
            event=event,
            chat_provider_id=provider_id,
            # contexts=context,  # type: ignore
            tools=tools,
            system_prompt=self.persona.system_prompt+dialog_prompt,
            prompt=additional_prompt,
            max_steps=30,  # Agent 最大执行步骤
            tool_call_timeout=60,  # 工具调用超时时间
        )

        assert llm_resp.result_chain is not None, "LLM 生成结果不能为空"
        assert isinstance(llm_resp.result_chain, MessageChain), "LLM 生成结果类型错误，应该为 MessageChain"

        prefix = f"[{self.p_list[self.active_idx]['name']}]说: \n"
        for i, part in enumerate(llm_resp.result_chain.chain):
            if isinstance(part, Plain):
                if not part.text.startswith(prefix):
                    llm_resp.result_chain.chain[i].text = prefix + part.text

        return llm_resp

    @filter.command("mulper_continue", alias={'mpc', '人格继续'})
    async def mulper_continue(self, event: AstrMessageEvent, idx: Optional[int] = None):
        """让llm按当前上下文继续生成"""
        if idx is not None:
            if idx < 1 or idx > len(self.p_list):
                yield event.plain_result(f"无效的人格编号，请输入1-{len(self.p_list)}之间的数字")
                return
            await self._switch_persona(idx)

        umo = event.unified_msg_origin
        provider_id = await self.context.get_current_chat_provider_id(umo)
        curr_cid = await self.conv_mgr.get_curr_conversation_id(umo)
        if curr_cid is None:
            # 如果当前没有对话，先创建一个对话
            curr_cid = await self.conv_mgr.new_conversation(umo)
        conversation = await self.conv_mgr.get_conversation(umo, curr_cid)
        assert conversation is not None, "当前对话不能为空"

        context = json.loads(conversation.history)
        llm_resp = await self._llm_request(event, context, provider_id, curr_cid)

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
    async def mulper_message(self, event: AstrMessageEvent, message: str, idx: Optional[int] = None):
        """让llm按当前上下文+新消息继续生成"""
        user_prefix = f"[{self.user_name}]说: \n"
        user_mgs = UserMessageSegment(content=[TextPart(text=user_prefix + message)])

        if idx is not None:
            if idx < 1 or idx > len(self.p_list):
                yield event.plain_result(f"无效的人格编号，请输入1-{len(self.p_list)}之间的数字")
                return
            await self._switch_persona(idx)

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
        llm_resp = await self._llm_request(event, context, provider_id, curr_cid)

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

    async def _context_summary(self, event: AstrMessageEvent, context: list[dict],
                               provider_id: str, cid: str, save: bool) -> LLMResponse:
        """总结对话上下文的公共方法，添加系统提示等"""
        extracted_context = self._extract_context(context)
        world_state = ''
        for ws in self.config["world_state"]:
            if ws["conversation_id"] == cid:
                world_state = ws["state"]
                break

        if world_state:
            user_prompt = "MODE: UPDATE\n\n"
            user_prompt += f"【世界状态】：\n{world_state}\n\n"
            user_prompt += f"【新对话】：\n{extracted_context}"
        else:
            user_prompt = "MODE: EXTRACT\n\n"
            user_prompt += f"【对话历史】：\n{extracted_context}"

        logger.info(f"使用的系统提示: {self.summary_prompt}")
        logger.info(f"使用的用户提示: {user_prompt}")

        llm_resp: LLMResponse = await self.context.llm_generate(
            event=event,
            chat_provider_id=provider_id,
            # contexts=context,  # type: ignore
            system_prompt=self.summary_prompt,
            prompt=user_prompt,
        )

        new_state = llm_resp.completion_text

        if save:
            if world_state:
                for i, ws in enumerate(self.config["world_state"]):
                    if ws["conversation_id"] == cid:
                        self.config["world_state"][i]["state"] = new_state
                        logger.info(f"对话 {cid} 的世界状态已更新")
            else:
                self.config["world_state"] += [{
                    "__template_key": "state_template",
                    "conversation_id": cid,
                    "state": new_state,
                }]
                logger.info(f"对话 {cid} 的世界状态已创建")
            self.config.save_config()

            conv_log_file = f"conversation_{cid}_log.txt"
            with open(f"{self.data_path}/{conv_log_file}", "a", encoding="utf-8") as f:
                f.write(f"【对话历史记录】\n{json.dumps(context, ensure_ascii=False)}\n\n")

            summary_log_file = f"conversation_{cid}_summary.txt"
            with open(f"{self.data_path}/{summary_log_file}", "a", encoding="utf-8") as f:
                f.write(f"【对话总结】\n{llm_resp.completion_text}\n\n")

            if len(context) > self.config['max_context_length']:
                await self.conv_mgr.db.update_conversation(
                    cid=cid,
                    content=context[-self.config['max_context_length']:],  # 保留最近的消息，删除较早的消息
                )
                logger.info(f"对话 {cid} 历史记录已清理，原对话记录和总结已保存到 {self.data_path} 目录下")

        return llm_resp

    @filter.command("mulper_summary", alias={'mpsu', '人格总结'})
    async def mulper_summary(self, event: AstrMessageEvent, save=True, cid: Optional[str] = None):
        """查看当前对话上下文信息，参数 cid 可选，默认为当前对话"""
        umo = event.unified_msg_origin
        provider_id = await self.context.get_current_chat_provider_id(umo)
        msg = ''
        if cid is None:
            curr_cid = await self.conv_mgr.get_curr_conversation_id(umo)
            if curr_cid is None:
                # 如果当前没有对话，先创建一个对话
                curr_cid = await self.conv_mgr.new_conversation(umo)
            cid = curr_cid
            msg += f"使用当前对话 ID: {cid}\n"
        else:
            msg += f"使用指定对话 ID: {cid}\n"

        conversation = await self.conv_mgr.get_conversation(umo, cid)
        assert conversation is not None, "当前对话不能为空"

        context = json.loads(conversation.history)
        msg += f"当前对话上下文包含{len(context)}条消息"
        yield event.plain_result(msg)

        if len(context) <= self.config['state_update_every']:
            yield event.plain_result("当前对话上下文过短，无需总结")
            return

        llm_resp = await self._context_summary(event, context, provider_id, cid, save=save)

        yield event.chain_result(llm_resp.result_chain.chain)  # type: ignore

        # @filter.command("mulper_delete", alias={'mpd', '人格删除'})
        # async def mulper_delete(self, event: AstrMessageEvent, cid: Optional[str] = None):
        #     """删除最新一条对话记录，参数 cid 可选，默认为当前对话"""
        #     umo = event.unified_msg_origin
        #     if cid is None:
        #         curr_cid = await self.conv_mgr.get_curr_conversation_id(umo)
        #         if curr_cid is None:
        #             yield event.plain_result("当前没有对话可删除")
        #             return
        #         cid = curr_cid
        #         yield event.plain_result(f"使用当前对话 ID: {cid}")
        #     else:
        #         yield event.plain_result(f"使用指定对话 ID: {cid}")

        #     conversation = await self.conv_mgr.get_conversation(umo, cid)
        #     assert conversation is not None, "当前对话不能为空"
        #     context = json.loads(conversation.history)
        #     if len(context) == 0:
        #         yield event.plain_result("当前对话没有消息可删除")
        #         return

        #     popped_context = context.pop()
        #     yield event.plain_result(f"已删除最新一条消息: {popped_context}")
        #     await self.conv_mgr.db.update_conversation(
        #         cid=cid,
        #         content=context,
        #     )

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
