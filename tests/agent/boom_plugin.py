"""测试用插件：入口先注册服务再抛异常，用于验证加载器的回滚

不放在 agent/plugins/ 下 —— 它不是产品插件，只是测试夹具。
"""

from typing import Callable, Optional

#: 该服务若出现在上下文里，说明回滚失败
LEAKED_SERVICE = "boom.leaked"


def setup(ctx, **config) -> Optional[Callable[[], None]]:
    ctx.provide(LEAKED_SERVICE, object())
    raise RuntimeError("插件入口故意失败")
