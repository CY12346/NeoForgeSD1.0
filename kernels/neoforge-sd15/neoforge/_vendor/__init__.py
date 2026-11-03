# -*- coding: utf-8 -*-
"""内置第三方小件（构建时从仓库 core/ 复制过来，别手改）。

  winproc.py   —— CREATE_NO_WINDOW 静音子进程（pythonw 下不闪黑框）
  i18n_tags.py —— 提示词中⇄英 / 补全 / 词库（读本机 SD 扩展的词典）

为什么要复制进来：内核要能整包搬走，不能反过来依赖壳的仓库目录。
改了源（core/）之后跑 `python tools/build_kernel.py` 重新 stage，
`tests/test_kernel_sd15.py` 会比对 sha256，忘了重建直接失败。
"""
