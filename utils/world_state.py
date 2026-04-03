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
from . import conversation as conv_helper


class WorldStateManager:
    def __init__(self, config: AstrBotConfig):
        self.config = config
        self.world_states: list[dict] = config.get("world_state", [])
        self.summary_prompt = config['summary_prompt']
        if not self.summary_prompt:
            raise ValueError("summary_prompt is required in config")

    def get(self, cid: str) -> str:
        for ws in self.world_states:
            if ws["conversation_id"] == cid:
                return ws["state"]
        return ''

    def update(self, cid: str, new_state: str) -> None:
        for i, ws in enumerate(self.world_states):
            if ws["conversation_id"] == cid:
                self.world_states[i]["state"] = new_state
                logger.info(f"对话 {cid} 的世界状态已更新")
                return
        # 如果没有找到对应的 conversation_id，则创建一个新的世界状态
        self.world_states.append({
            "__template_key": "state_template",
            "conversation_id": cid,
            "state": new_state,
        })
        logger.info(f"对话 {cid} 的世界状态已创建")
        self.config["world_state"] = self.world_states
        self.config.save_config()

    async def delete_unused(self, conv_mgr: ConversationManager):
        for ws in self.world_states:
            cid = ws['conversation_id']
            conv = await conv_mgr.db.get_conversation_by_id(cid=cid)
            if not conv:
                logger.warning(f"对话 {cid} 不存在，删除无效的世界状态配置")
                self.world_states.remove(ws)
        self.config["world_state"] = self.world_states
        self.config.save_config()

    async def summary(self, context: Context,
                      event: AstrMessageEvent,
                      dialog: str,
                      data_path: str,
                      save: bool) -> LLMResponse:
        """总结对话上下文的公共方法，添加系统提示等"""
        umo = event.unified_msg_origin
        provider_id = await context.get_current_chat_provider_id(umo)
        conv_mgr = context.conversation_manager
        cid = await conv_mgr.get_curr_conversation_id(umo)
        if cid is None:
            # 如果当前没有对话，先创建一个对话
            cid = await conv_mgr.new_conversation(umo)

        world_state = self.get(cid)

        if world_state:
            user_prompt = "MODE: UPDATE\n\n"
            user_prompt += f"【世界状态】：\n{world_state}\n\n"
            user_prompt += '\n------\n\n'
            user_prompt += f"【新对话】：\n{dialog}"
        else:
            user_prompt = "MODE: EXTRACT\n\n"
            user_prompt += f"【对话历史】：\n{dialog}"

        logger.info(f"使用的系统提示: {self.summary_prompt}")
        logger.info(f"使用的用户提示: {user_prompt}")

        llm_resp: LLMResponse = await context.llm_generate(
            event=event,
            chat_provider_id=provider_id,
            # contexts=context,  # type: ignore
            system_prompt=self.summary_prompt,
            prompt=user_prompt,
        )

        new_state = llm_resp.completion_text

        if save:
            self.update(cid, new_state)

            summary_log_file = f"conversation_{cid}_summary.txt"
            with open(f"{data_path}/{summary_log_file}", "a", encoding="utf-8") as f:
                f.write(f"【对话总结】\n{llm_resp.completion_text}\n\n")

        return llm_resp

    async def compress(self, context: Context,
                       event: AstrMessageEvent,
                       data_path: str,
                       save: bool) -> LLMResponse | None:
        """更新并压缩世界状态的公共方法"""
        umo = event.unified_msg_origin
        provider_id = await context.get_current_chat_provider_id(umo)
        conv_mgr = context.conversation_manager
        cid = await conv_mgr.get_curr_conversation_id(umo)
        if cid is None:
            # 如果当前没有对话，先创建一个对话
            cid = await conv_mgr.new_conversation(umo)

        world_state = self.get(cid)
        if not world_state:
            logger.warning(f"对话 {cid} 没有找到对应的世界状态，无法进行更新和压缩")
            return None

        user_prompt = "MODE: COMPRESS\n\n"
        user_prompt += f"【世界状态】：\n{world_state}\n\n"

        logger.info(f"使用的系统提示: {self.summary_prompt}")
        logger.info(f"使用的用户提示: {user_prompt}")

        llm_resp: LLMResponse = await context.llm_generate(
            event=event,
            chat_provider_id=provider_id,
            # contexts=context,  # type: ignore
            system_prompt=self.summary_prompt,
            prompt=user_prompt,
        )

        new_state = llm_resp.completion_text

        if save:
            self.update(cid, new_state)

            summary_log_file = f"conversation_{cid}_summary.txt"
            with open(f"{data_path}/{summary_log_file}", "a", encoding="utf-8") as f:
                f.write(f"【对话总结】\n{llm_resp.completion_text}\n\n")

        return llm_resp
