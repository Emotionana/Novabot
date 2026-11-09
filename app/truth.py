"""真实性策略：事实/能力/动作不许装懂；工作模式更严格。

不是给机器人开“说谎模式”，而是把“什么能开玩笑、什么必须确切”分开：
- 事实、能力、动作、承诺、记忆：必须有依据，不知道就说不知道
- 工作场景：只讲可验证结果，没执行就不说已执行
- 社交/角色扮演：可以保留语气、玩笑、傲娇，但不能用编造事实来撑人设
"""

from __future__ import annotations

import re

WORK_WORDS = (
    "工作", "项目", "代码", "文件", "目录", "删除", "删掉", "修改", "改一下",
    "创建", "新建", "写入", "读取", "发送", "上传", "下载", "安装", "部署",
    "运行", "执行", "命令", "终端", "配置", "设置", "接口", "API", "数据库",
    "日志", "测试", "审批", "权限", "任务", "脚本", "报错", "修复", "查一下",
    "查查", "看一下", "帮我改", "帮我删", "帮我建", "帮我发", "DSH",
)

_ACTION = re.compile(
    r"(已经|已|帮你|替你|给我|给你|刚刚|刚才)[^。！？\\n]{0,20}"
    r"(删除|删掉|修改|改好|创建|新建|写好|写完|发送|发出去|上传|下载|安装|运行|执行|"
    r"检查|看完|读完|设置|配置|打开|关闭|完成|处理|修好|部署|测试过)"
)
_NEGATED = re.compile(r"(没|没有|还没|未|不能|无法|不会|做不到|尚未|需要你|等你|打算|准备|稍后|待会)")


class TruthPolicy:
    def __init__(self, settings):
        self.settings = settings
        self._warm()

    def _warm(self) -> None:
        self.settings.get("truth.enabled")
        self.settings.get("truth.work_mode")
        self.settings.get("truth.strict_unknown")
        self.settings.get("truth.allow_roleplay")
        self.settings.get("truth.guard_actions")
        self.settings.get("truth.work_keywords")

    def _get(self, key, default=None):
        try:
            value = self.settings.get(key)
            return default if value is None else value
        except KeyError:
            return default

    @property
    def enabled(self) -> bool:
        return bool(self._get("truth.enabled", True))

    def work_context(self, text: str, history: str = "", *, is_owner: bool, is_private: bool) -> bool:
        if not self.enabled:
            return False
        mode = str(self._get("truth.work_mode", "auto"))
        if mode == "off":
            return False
        if mode == "always":
            return True
        if not (is_owner and is_private):
            return False
        hay = f"{text}\n{history}"
        words = self._get("truth.work_keywords", WORK_WORDS) or WORK_WORDS
        return any(str(w) in hay for w in words if str(w).strip())

    def prompt(self, *, work: bool) -> str:
        if not self.enabled:
            return ""
        base = (
            "【真实性规则】\n"
            "先分清你在说的是哪一类：事实、能力/权限、已经做过的动作、承诺、记忆，还是角色扮演/感受/玩笑。\n"
            "- 事实、能力、动作、承诺、记忆：必须真实。没有依据、没有工具结果、没查到时，直接说“我不确定”“我没查到”“我做不到”“我还没有实际执行”。\n"
            "- 不要编造文件路径、命令输出、审批状态、账号状态、记忆内容，也不要把“打算做”说成“已经做完”。\n"
            "- 不确定就说不确定；不知道不丢人，装懂才丢人。被指出说错时先承认，再给确切信息。\n"
            "- 角色扮演、语气、玩笑、个人偏好可以保留，但不能用编造的事实来撑人设。\n"
        )
        if not self._get("truth.allow_roleplay", True):
            base += "不要使用角色扮演式编造；只回答你确实知道的内容。\n"
        if work:
            base += (
                "【工作模式】当前是在和主人处理工作/文件/系统相关的事。\n"
                "- 只讲可验证的结果；没有实际执行成功就不要说“已完成/已删除/已修改/已发送”。\n"
                "- 工具报错、权限不足、DSH 没连上、账号离线，要原样说明，不要用“应该好了”糊过去。\n"
                "- 不确定的地方明确标出来，并给出下一步需要确认什么。\n"
            )
        else:
            base += "现在是普通聊天：可以放松、可以开玩笑，但事实性问题仍按上面的规则。\n"
        return base

    def review(self, text: str, *, work: bool, tools: dict | None = None) -> str:
        """工作模式下，没有工具证据却声称动作完成时，改成诚实说明。"""
        if not self.enabled or not work or not self._get("truth.guard_actions", True):
            return text
        stats = tools or {}
        calls = int(stats.get("calls") or 0)
        results = int(stats.get("results") or 0)
        errors = int(stats.get("errors") or 0)
        # 有工具调用且至少有一个结果，先视为有证据；不误伤 DSH 工具
        if calls > 0 and results > 0 and errors == 0:
            return text
        if not _ACTION.search(text):
            return text
        # 带否定/将来/条件的句子不算“谎称完成”
        sentences = re.split(r"(?<=[。！？!?\\n])", text)
        changed = False
        out = []
        for sentence in sentences:
            if _ACTION.search(sentence) and not _NEGATED.search(sentence):
                out.append("这件事我还没有实际执行成功，不能跟你说已经完成。")
                changed = True
            else:
                out.append(sentence)
        if not changed:
            return text
        return "".join(out).strip()
