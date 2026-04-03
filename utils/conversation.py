from typing import Literal
from astrbot.core.agent.message import Message, AssistantMessageSegment, TextPart
from astrbot.core.conversation_mgr import ConversationManager
from astrbot.api.provider import LLMResponse
import json
from astrbot.api import logger


def extract_dialog(history: list[dict], prefixs: list[str]) -> str:
    """从消息记录中提取对话上下文，去掉身份标签等无关信息"""
    dialog = ''
    for msg in history:
        if msg['role'] in ['assistant', 'user']:
            text: str = msg['content'][0]['text']
            for p in prefixs:
                if text.startswith(p):
                    text = text.replace('\n\n', '\n')
                    dialog += text + '\n\n'
                    break

    return dialog


async def add_conversation_history(conv_mgr: ConversationManager,
                                   cid: str, text: str,
                                   role: Literal['user', 'assistant']) -> None:
    """将消息添加到对话历史中"""
    message = Message(role=role, content=[TextPart(text=text)])
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


async def clear_conversation_history(conv_mgr: ConversationManager,
                                     cid: str, data_path: str,
                                     max_context_length: int = 10) -> list[dict]:
    """清理对话历史，保留对话总结等重要信息"""
    conv = await conv_mgr.db.get_conversation_by_id(cid=cid)
    if not conv:
        raise Exception(f"Conversation with id {cid} not found")
    history = conv.content or []

    conv_log_file = f"conversation_{cid}_log.txt"
    with open(f"{data_path}/{conv_log_file}", "a", encoding="utf-8") as f:
        f.write(f"【对话历史记录】\n{json.dumps(history, ensure_ascii=False)}\n\n")

    if len(history) > max_context_length:
        await conv_mgr.db.update_conversation(
            cid=cid,
            content=history[-max_context_length:],  # 保留最近的消息，删除较早的消息
        )
        logger.info(f"对话 {cid} 历史记录已清理，原对话记录和总结已保存到 {data_path} 目录下")

    return history[-max_context_length:]
