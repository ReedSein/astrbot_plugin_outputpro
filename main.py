import asyncio
import random
import re
import emoji
from pydantic import BaseModel
from collections import defaultdict
from astrbot.api.event import filter
from astrbot import logger
from astrbot.api.star import Context, Star, register
from astrbot.core import AstrBotConfig
from astrbot.core.platform.astr_message_event import AstrMessageEvent
from astrbot.core.message.components import (
    Face,
    Image,
    Plain,
)


class GroupState(BaseModel):
    gid: str
    last_msg_chain: list = []


class StateManager:
    """线程安全的内存状态管理"""
    _groups: dict[str, GroupState] = {}
    _locks: defaultdict[str, asyncio.Lock] = defaultdict(asyncio.Lock)

    @classmethod
    async def get_group(cls, gid: str) -> GroupState:
        async with cls._locks[gid]:
            if gid not in cls._groups:
                cls._groups[gid] = GroupState(gid=gid)
            return cls._groups[gid]

    @classmethod
    async def get_last_msg_chain(cls, gid: str) -> list:
        group = await cls.get_group(gid)
        async with cls._locks[gid]:
            return group.last_msg_chain

    @classmethod
    async def set_last_msg_chain(cls, gid: str, chain: list):
        group = await cls.get_group(gid)
        async with cls._locks[gid]:
            group.last_msg_chain = chain


@register(
    "astrbot_plugin_outputpro",
    "Zhalslar",
    "输出增强插件：报错拦截、文本清洗 (并发安全版)",
    "2.0.0",
)
class BetterIOPlugin(Star):
    def __init__(self, context: Context, config: AstrBotConfig):
        super().__init__(context)
        self.conf = config

    @filter.on_decorating_result(priority=15)
    async def on_message(self, event: AstrMessageEvent):
        """发送消息前的预处理 (已进行并发加固)"""
        try:
            # --- 安全访问配置 ---
            intercept_error = self.conf.get("intercept_error", True)
            error_keywords = self.conf.get("error_keywords", [])
            clean_text_length = self.conf.get("clean_text_length", 100)
            clean_emoji = self.conf.get("clean_emoji", True)
            clean_punctuation = self.conf.get("clean_punctuation", r"[^\w\s\u4e00-\u9fa5]")
            remove_lead = self.conf.get("remove_lead", [])

            # 过滤空消息
            result = event.get_result()
            chain = result.chain
            if not chain:
                event.stop_event()
                return

            gid: str = event.get_group_id()
            if not gid: # 私聊等情况不处理状态
                return

            # 拦截重复消息 (线程安全)
            last_chain = await StateManager.get_last_msg_chain(gid)
            if chain == last_chain:
                logger.debug(f"[OutputPro] 在群 {gid} 检测到重复消息，已拦截。")
                event.stop_event()
                return
            await StateManager.set_last_msg_chain(gid, chain)

            # 拦截错误信息(根据关键词拦截)
            if intercept_error or not event.is_admin():
                err_str = result.get_plain_text() if hasattr(result, "get_plain_text") else ""
                if any(keyword in err_str for keyword in error_keywords if keyword):
                    try:
                        event.set_result(event.plain_result(""))
                        logger.debug("[OutputPro] 已将错误回复内容替换为空消息")
                    except AttributeError:
                        event.stop_event()
                        logger.debug("[OutputPro] 不支持 set_result，已阻止错误消息发送")
                    return

            # 过滤不支持的消息类型
            if not all(isinstance(comp, (Plain, Image, Face)) for comp in chain):
                return

            # 清洗文本消息
            if chain and isinstance(chain[-1], Plain):
                end_seg = chain[-1]
                if len(end_seg.text) < clean_text_length:
                    # 清洗emoji
                    if clean_emoji:
                        end_seg.text = emoji.replace_emoji(end_seg.text, replace="")
                    # 清洗标点符号
                    if clean_punctuation:
                        end_seg.text = re.sub(clean_punctuation, "", end_seg.text)
                    # 去除指定开头字符
                    if remove_lead:
                        for lead in remove_lead:
                            if end_seg.text.startswith(lead):
                                end_seg.text = end_seg.text[len(lead):]
        except Exception as e:
            logger.error(f"[OutputPro] 在处理消息时发生意外错误: {e}", exc_info=True)
            # 即使发生错误，也不要中断流水线，让后续的插件有机会运行
            return
