# 第三方组件与商标声明 / THIRD-PARTY NOTICES

本项目运行时不依赖任何第三方库，也不从外部 CDN 加载资源；但界面内联了少量第三方图标的 SVG 路径，并引用了设备自带的字体与在线授时服务。以下声明随本项目的所有副本一并提供，满足相应许可中「版权与许可声明须随副本保留」的要求。

## 1. Simple Icons —— 品牌图标（支付宝、微信）

- 来源：https://simpleicons.org/
- 用途：`index.html` 中 `PAY_ICONS.alipay` / `PAY_ICONS.wechat` 的 SVG 路径，仅显示在用户自建账户名称之前，用于标识账户类型。
- 许可：**CC0 1.0 Universal**（公共领域贡献，对 SVG 文件本身不要求署名）。全文：https://creativecommons.org/publicdomain/zero/1.0/
- **商标声明**：CC0 只覆盖图标绘制文件的著作权，**不覆盖商标权**。支付宝、微信及相关标识分别为其权利人的商标。本项目仅作指示性／描述性使用，不表示与相关权利人存在合作、授权、赞助或官方关联；未将这些标识用于应用图标、favicon 或产品名称（应用图标与 favicon 均为项目自行绘制的「鱼 + ￥」图形，青绿圆角方块 / 圆形底，不含任何品牌标识），README 截图仅展示其在账户列表中的指示性用法。若权利人不希望其标识以该方式出现，可联系移除。

## 2. Lucide —— 线性界面图标

- 来源：https://lucide.dev/
- 用途：界面中的日历、下拉箭头、现金、银行卡等线性图标的 SVG 路径。
- 许可：**ISC License**；其中部分图标派生自 Feather 项目，适用 **MIT License**。
- 依许可要求，随附许可全文：

### ISC License（Lucide）

```
Copyright (c) for portions of Lucide are held by Cole Bemis 2013-2022 as part of Feather (MIT).
All other copyright (c) for Lucide are held by Lucide Contributors 2022.

Permission to use, copy, modify, and/or distribute this software for any purpose
with or without fee is hereby granted, provided that the above copyright notice
and this permission notice appear in all copies.

THE SOFTWARE IS PROVIDED "AS IS" AND THE AUTHOR DISCLAIMS ALL WARRANTIES WITH
REGARD TO THIS SOFTWARE INCLUDING ALL IMPLIED WARRANTIES OF MERCHANTABILITY
AND FITNESS. IN NO EVENT SHALL THE AUTHOR BE LIABLE FOR ANY SPECIAL, DIRECT,
INDIRECT, OR CONSEQUENTIAL DAMAGES OR ANY DAMAGES WHATSOEVER RESULTING FROM
LOSS OF USE, DATA OR PROFITS, WHETHER IN AN ACTION OF CONTRACT, NEGLIGENCE OR
OTHER TORTIOUS ACTION, ARISING OUT OF OR IN CONNECTION WITH THE USE OR
PERFORMANCE OF THIS SOFTWARE.
```

### MIT License（Feather 派生图标）

```
The MIT License (MIT)

Copyright (c) 2013-present Cole Bemis

Permission is hereby granted, free of charge, to any person obtaining a copy
of this software and associated documentation files (the "Software"), to deal
in the Software without restriction, including without limitation the rights
to use, copy, modify, merge, publish, distribute, sublicense, and/or sell
copies of the Software, and to permit persons to whom the Software is
furnished to do so, subject to the following conditions:

The above copyright notice and this permission notice shall be included in
all copies or substantial portions of the Software.

THE SOFTWARE IS PROVIDED "AS IS", WITHOUT WARRANTY OF ANY KIND, EXPRESS OR
IMPLIED, INCLUDING BUT NOT LIMITED TO THE WARRANTIES OF MERCHANTABILITY,
FITNESS FOR A PARTICULAR PURPOSE AND NONINFRINGEMENT. IN NO EVENT SHALL THE
AUTHORS OR COPYRIGHT HOLDERS BE LIABLE FOR ANY CLAIM, DAMAGES OR OTHER
LIABILITY, WHETHER IN AN ACTION OF CONTRACT, TORT OR OTHERWISE, ARISING FROM,
OUT OF OR IN CONNECTION WITH THE SOFTWARE OR THE USE OR OTHER DEALINGS IN
THE SOFTWARE.
```

## 3. 字体

CSS 字体栈中列出 `Inter Tight`、`Inter`、`HarmonyOS Sans SC`、`PingFang SC`、`Microsoft YaHei` 等名称，**仅作为用户设备上已安装字体的回退顺序**；本项目未打包、内嵌、修改或分发任何字体文件，favicon 与应用图标中的字符也由系统字体渲染。各字体的权利归各自权利人，字体名称的出现不构成授权或关联声明。

## 4. 运行时与在线服务

| 组件 / 服务 | 使用方式 | 说明 |
| --- | --- | --- |
| Python 标准库 | 通过系统 Python 调用 | PSF License；未分发解释器或标准库副本 |
| SQLite | 通过 `sqlite3` 标准库调用 | 未分发数据库引擎二进制 |
| 阿里云 NTP / HTTP Date | 可选授时，失败自动回退本地时间 | 仅发起网络请求，未复制其代码或数据 |
| Gitee 等代码托管平台 | 源码托管与分发 | 托管服务不改变本项目权属 |

## 5. AI 辅助创作与权属说明

本项目在开发过程中使用 AI 工具辅助生成与修改代码、文档与图形，但需求定义、技术方案选择、结构编排、逐轮修改、验证测试、数据模型与最终取舍均由项目作者完成。

在主要法域，完全由 AI 自主生成且缺乏人类独创性贡献的内容通常不受著作权保护；是否受保护取决于人类对表达的实际控制与创造性贡献。本项目仅就作者作出实质性创作与编排的部分主张权利，并按 `LICENSE`（MIT License）授权他人使用；对纯 AI 自动生成、缺乏人类独创性贡献的部分不主张独占权。项目保留需求记录、迭代记录、改动前后快照、版本提交历史与验证报告作为创作过程证据。

## 6. 无担保与免责

本项目按「现状」提供，不附带任何明示或默示担保；不对数据丢失、金额计算偏差、服务中断或未授权访问承担责任。本文件为事实性说明，不构成法律意见；MIT License 不授予项目名称与标识的商标权，商用分发时仍需遵守本文件列出的第三方许可与商标要求。

第三方组件与商标的完整权利信息以各自权利人的官方发布为准；本文件与 `LICENSE` 不一致时，第三方权利以本文件及其引用许可为准。
