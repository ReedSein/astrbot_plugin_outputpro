import random
import re
import emoji
from pydantic import BaseModel
from astrbot.api.event import filter
from astrbot import logger
from astrbot.api.star import Context, Star, register
from astrbot.core import AstrBotConfig
from astrbot.core.platform.astr_message_event import AstrMessageEvent
# 导入的组件中已经移除了 At 和 Reply
from astrbot.core.message.components import (
    Face,
    Image,
    Plain,
)


class GroupState(BaseModel):
    gid: str
    last_msg: str = ""
    # 未来的拓展属性


class StateManager:
    """内存状态管理"""

    _groups: dict[str, GroupState] = {}

    @classmethod
    def get_group(cls, gid: str) -> GroupState:
        if gid not in cls._groups:
            cls._groups[gid] = GroupState(gid=gid)
        return cls._groups[gid]


@register(
    "astrbot_plugin_outputpro",
    "Zhalslar",
    "输出增强插件：报错拦截、文本清洗", # 描述已更新
    "1.0.2", # 版本号微调
)
class BetterIOPlugin(Star):
    def __init__(self, context: Context, config: AstrBotConfig):
        super().__init__(context)
        self.conf = config

    @filter.on_decorating_result(priority=15)
    async def on_message(self, event: AstrMessageEvent):
        """发送消息前的预处理"""
        # 过滤空消息
        result = event.get_result()
        chain = result.chain
        if not chain:
            event.stop_event()
            return

        gid: str = event.get_group_id()
        g: GroupState = StateManager.get_group(gid)

        # 拦截重复消息
        if chain == g.last_msg:
            event.stop_event()
            return
        g.last_msg = event.message_str

        # 拦截错误信息(根据关键词拦截)
        if self.conf["intercept_error"] or not event.is_admin():
            err_str = (
                result.get_plain_text() if hasattr(result, "get_plain_text") else ""
            )
            if next(
                (
                    keyword
                    for keyword in self.conf["error_keywords"]
                    if keyword in err_str
                ),
                None,
            ):
                try:
                    event.set_result(event.plain_result(""))
                    logger.debug("已将回复内容替换为空消息")
                except AttributeError:
                    event.stop_event()
                    logger.debug("不支持 set_result，尝试使用 stop_event 阻止消息发送")
                return

        # 过滤不支持的消息类型
        if not all(isinstance(comp, (Plain, Image, Face)) for comp in chain):
            return

        # 清洗文本消息
        end_seg = chain[-1]
        if (
            isinstance(end_seg, Plain)
            and len(end_seg.text) < self.conf["clean_text_length"]
        ):
            # 清洗emoji
            if self.conf["clean_emoji"]:
                end_seg.text = emoji.replace_emoji(end_seg.text, replace="")
            # 清洗标点符号
            if self.conf["clean_punctuation"]:
                end_seg.text = re.sub(self.conf["clean_punctuation"], "", end_seg.text)
            # 去除指定开头字符
            if self.conf["remove_lead"]:
                for remove_lead in self.conf["remove_lead"]:
                    if end_seg.text.startswith(remove_lead):
                        end_seg.text = end_seg.text[len(remove_lead) :]

        # --- 以下负责随机@和引用的代码块已被完全移除 ---
        # if event.get_platform_name() == "aiocqhttp":
        #     ...