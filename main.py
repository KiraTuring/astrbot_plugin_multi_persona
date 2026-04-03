from typing import Optional
from astrbot.api.event import filter, AstrMessageEvent
from astrbot.api.star import Context, Star
from astrbot.api.provider import LLMResponse
from astrbot.api import logger, AstrBotConfig
from astrbot.core.utils.astrbot_path import get_astrbot_data_path
import json
import asyncio
import os

from .utils.actor import Actor, request_actor_llm
from .utils import conversation as conv_helper
from .utils.world_state import WorldStateManager


class MultiPersonaPlugin(Star):
    def __init__(self, context: Context, config: AstrBotConfig):
        super().__init__(context)
        self.config = config
        self.conv_mgr = self.context.conversation_manager
        self.data_path = get_astrbot_data_path() + f"/plugin_data/{self.name}"
        os.makedirs(self.data_path, exist_ok=True)

    async def initialize(self):
        self.user_name = self.config['user_name']
        self.mode = self.config['mode']
        self.actor_list = await Actor.create_all_with_context(self.config, self.context)
        self.active_idx = 0  # 默认选择第一个人格

        self.dialog_prompt = self.config['dialog_prompt']

        self.ws_mgr = WorldStateManager(self.config)
        await self.ws_mgr.delete_unused(self.conv_mgr)  # 删除无效的世界状态配置

        self.all_prefix = [a.prefix for a in self.actor_list]
        self.user_prefix = f"[{self.user_name}]说: \n"

    @filter.command("mulper_info", alias={'mpi', '人格信息'})
    async def mulper_info(self, event: AstrMessageEvent):
        """查看目前的人格设定和对话上下文等信息"""
        msg = f"目前已有{len(self.actor_list)}个人格设置:\n"
        for i, actor in enumerate(self.actor_list, 1):
            if i == self.active_idx + 1:
                msg += f"人格{i}: {actor.persona_id} (当前使用)\n"
            else:
                msg += f"人格{i}: {actor.persona_id}\n"

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
        old_p = self.actor_list[self.active_idx].persona_id
        if idx is None:
            new_p = await self._switch_persona()
        else:
            if idx < 1 or idx > len(self.actor_list):
                yield event.plain_result(f"无效的人格编号，请输入1-{len(self.actor_list)}之间的数字")
                return
            self.active_idx = idx - 1
            new_p = self.actor_list[self.active_idx].persona_id

        msg = f"从 {old_p} 人格切换为 {new_p} 人格"
        yield event.plain_result(msg)

    async def _switch_persona(self, idx: Optional[int] = None) -> str:
        """切换目前使用的人格"""
        if idx is not None:
            self.active_idx = idx - 1
        else:
            self.active_idx += 1
            if self.active_idx >= len(self.actor_list):
                self.active_idx -= len(self.actor_list)
            elif self.active_idx < 0:
                self.active_idx += len(self.actor_list)
        new_p = self.actor_list[self.active_idx].persona_id
        return new_p

    async def _actor_request(self, event: AstrMessageEvent) -> LLMResponse:
        umo = event.unified_msg_origin
        cid = await self.conv_mgr.get_curr_conversation_id(umo)
        if cid is None:
            # 如果当前没有对话，先创建一个对话
            cid = await self.conv_mgr.new_conversation(umo)

        actor = self.actor_list[self.active_idx]

        conv = await self.conv_mgr.db.get_conversation_by_id(cid=cid)
        assert conv is not None and conv.content
        context: list[dict] = conv.content
        dialog = conv_helper.extract_dialog(context, self.all_prefix+[self.user_prefix])

        # TODO: 改成LLM判断是否需要总结更新世界状态，而不是单纯根据消息记录条数判断
        if len(context) > self.config['max_context_length']+self.config['state_update_every']:
            logger.warning(f"对话上下文过长，当前消息记录条数: {len(context)}，将自动更新世界状态并清理对话历史")
            context = await conv_helper.clear_conversation_history(self.conv_mgr, cid,
                                                                   self.data_path,
                                                                   self.config['max_context_length'])
            asyncio.create_task(self.ws_mgr.summary(self.context, event, dialog, self.data_path, save=True))

        llm_resp = await request_actor_llm(actor, self.context, event, self.config, dialog)

        await conv_helper.add_conversation_history(
            self.conv_mgr,
            cid=cid,
            text=llm_resp.completion_text,
            role='assistant'
        )
        return llm_resp

    @filter.command("mulper_continue", alias={'mpc', '人格继续'})
    async def mulper_continue(self, event: AstrMessageEvent, idx: Optional[int] = None):
        """让llm按当前上下文继续生成"""
        if idx is not None:
            if idx < 1 or idx > len(self.actor_list):
                yield event.plain_result(f"无效的人格编号，请输入1-{len(self.actor_list)}之间的数字")
                return
            await self._switch_persona(idx)

        event.stop_event()
        await self._actor_request(event)
        # yield event.chain_result(llm_resp.result_chain.chain)  # type: ignore

        if self.mode == 'switch':
            old_p = self.actor_list[self.active_idx].persona_id
            new_p = await self._switch_persona()
            msg = f"从 {old_p} 人格自动切换为 {new_p} 人格"
            logger.info(msg)
            # yield event.plain_result(msg)

    @filter.command("mulper_loop", alias={'mpl', '人格循环'})
    async def mulper_loop(self, event: AstrMessageEvent, rounds: int = 2):
        """让llm按当前上下文连续生成多轮"""
        event.stop_event()
        yield event.plain_result(f"开始连续生成{rounds}轮，每轮结束后将自动切换人格")  # type: ignore

        for turn in range(rounds):
            await self._actor_request(event)
            await asyncio.sleep(2)  # 每轮生成后等待2秒，避免请求过快被限制
            # yield event.chain_result(llm_resp.result_chain.chain)  # type: ignore

            old_p = self.actor_list[self.active_idx].persona_id
            new_p = await self._switch_persona()
            msg = f"从 {old_p} 人格自动切换为 {new_p} 人格"
            logger.info(msg)
            # yield event.plain_result(msg)

    @filter.command("mulper_message", alias={'mpm', '人格消息'})
    async def mulper_message(self, event: AstrMessageEvent, message: str, idx: Optional[int] = None):
        """让llm按当前上下文+新消息继续生成"""
        if idx is not None:
            if idx < 1 or idx > len(self.actor_list):
                yield event.plain_result(f"无效的人格编号，请输入1-{len(self.actor_list)}之间的数字")
                return
            await self._switch_persona(idx)

        umo = event.unified_msg_origin
        curr_cid = await self.conv_mgr.get_curr_conversation_id(umo)
        if curr_cid is None:
            # 如果当前没有对话，先创建一个对话
            curr_cid = await self.conv_mgr.new_conversation(umo)

        await conv_helper.add_conversation_history(
            self.conv_mgr,
            cid=curr_cid,
            text=self.user_prefix + message,
            role='user'
        )

        event.stop_event()
        await self._actor_request(event)
        # yield event.chain_result(llm_resp.result_chain.chain)  # type: ignore

        if self.mode == 'switch':
            old_p = self.actor_list[self.active_idx].persona_id
            new_p = await self._switch_persona()
            msg = f"从 {old_p} 人格自动切换为 {new_p} 人格"
            logger.info(msg)
            # yield event.plain_result(msg)

    @filter.command("mulper_summary", alias={'mpsu', '人格总结'})
    async def mulper_summary(self, event: AstrMessageEvent, save=True):
        """查看当前对话上下文信息，参数 cid 可选，默认为当前对话"""
        umo = event.unified_msg_origin
        msg = ''
        curr_cid = await self.conv_mgr.get_curr_conversation_id(umo)
        if curr_cid is None:
            # 如果当前没有对话，先创建一个对话
            curr_cid = await self.conv_mgr.new_conversation(umo)
        cid = curr_cid
        msg += f"使用当前对话 ID: {cid}\n"

        conversation = await self.conv_mgr.get_conversation(umo, cid)
        assert conversation is not None, "当前对话不能为空"

        history = json.loads(conversation.history)
        msg += f"当前对话上下文包含{len(history)}条消息"
        yield event.plain_result(msg)

        if len(history) <= self.config['state_update_every']:
            yield event.plain_result("当前对话上下文过短，无需总结")
            return

        dialog = conv_helper.extract_dialog(history, self.all_prefix+[self.user_prefix])
        llm_resp = await self.ws_mgr.summary(self.context, event, dialog, self.data_path, save=save)

        yield event.chain_result(llm_resp.result_chain.chain)  # type: ignore

    @filter.command("mulper_compress", alias={'mpco', '人格压缩'})
    async def mulper_compress(self, event: AstrMessageEvent, save=True):
        """查看当前对话上下文信息，参数 cid 可选，默认为当前对话，并对世界状态进行压缩更新"""
        llm_resp = await self.ws_mgr.compress(self.context, event, self.data_path, save=save)
        yield event.chain_result(llm_resp.result_chain.chain)  # type: ignore

    async def terminate(self):
        """可选择实现异步的插件销毁方法，当插件被卸载/停用时会调用。"""
