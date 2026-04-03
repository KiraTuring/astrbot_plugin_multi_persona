

def _modify_roles(context: list[dict], prefixs: list[str], active_idx: int) -> list[dict]:
    """修改消息记录中的角色，将身份标签不是自己的助手消息改为用户消息"""
    other_prefix = [p for i, p in enumerate(prefixs) if i != active_idx]

    for j in range(len(context)):
        if context[j]['role'] == 'assistant':
            for p in other_prefix:
                if context[j]['content'][0]['text'].startswith(p):
                    context[j]['role'] = 'user'
    return context

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
