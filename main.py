import random
import re
import emoji
from pydantic import BaseModel
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
    "输出增强插件：报错拦截、文本清洗、CoT最终防线", # 描述已更新
    "2.0.0", # 版本号升级
)
class BetterIOPlugin(Star):
    def __init__(self, context: Context, config: AstrBotConfig):
        super().__init__(context)
        self.conf = config
        # 为CoT检测预编译正则表达式，提高效率
        self.final_reply_pattern = re.compile(r"最终的罗莎回复[:：]?\s*", re.IGNORECASE)

    @filter.on_decorating_result(priority=110) # 优先级提升，确保在IntelligentRetry之后运行
    async def on_message(self, event: AstrMessageEvent):
        """发送消息前的预处理，包含CoT最终防线"""
        result = event.get_result()
        if not result or not result.chain:
            event.stop_event()
            return
            
        chain = result.chain
        plain_text = result.get_plain_text()

        # --- 新增：CoT结构最终防线 ---
        if self.conf.get("enable_cot_failsafe", True):
            has_os_tag = "<罗莎内心OS>" in plain_text
            has_final_reply_tag = self.final_reply_pattern.search(plain_text)

            if has_os_tag and has_final_reply_tag:
                # 检测到未处理的CoT结构，这是一个严重的逻辑失败信号
                logger.critical(
                    "[OutputPro] 最终防线触发！检测到本应被处理的CoT结构。"
                    "这表明 IntelligentRetryWithCoT 插件的过滤逻辑可能已失效。"
                    "为防止原始思维链泄露，此消息已被拦截。"
                )
                event.stop_event() # 拦截消息
                return # 终止后续所有处理
        # --- 防线结束 ---

        gid: str = event.get_group_id()
        g: GroupState = StateManager.get_group(gid)

        # 拦截重复消息
        if chain == g.last_msg:
            event.stop_event()
            return
        g.last_msg = event.message_str

        # 拦截错误信息(根据关键词拦截)
        if self.conf["intercept_error"] or not event.is_admin():
            if next(
                (
                    keyword
                    for keyword in self.conf["error_keywords"]
                    if keyword in plain_text
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