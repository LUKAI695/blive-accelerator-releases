# XHS Studio · 小红书运营工作台

在自己电脑上运行的小红书运营工具：管理账号、写笔记、排期、自动发布图文，并可以用 AI 辅助写文案。

## 功能

| 模块 | 说明 |
| --- | --- |
| 账号管理 | 可以添加多个自己的账号。弹出浏览器窗口后用小红书 App 扫码登录，登录态保存在本机（每个账号一个独立的浏览器目录），**不保存密码** |
| 内容库 | 编辑标题（≤20 字）、正文（≤1000 字）、话题标签和图片（1–18 张，第一张为封面）。笔记分为草稿、定时中、发布中、已发布、失败几种状态 |
| 发布 | “立即发布”或“定时发布”。程序会自动打开 [创作服务平台](https://creator.xiaohongshu.com)，上传图片，填写标题、正文和话题，然后点击发布 |
| 节奏保护 | 同一账号两次发布默认至少间隔 30 分钟，24 小时内最多发 3 篇。超出限制的定时笔记会顺延，不会被丢弃 |
| AI 文案 | 输入主题、风格和要点，由 Claude 生成标题、正文和标签草稿，人工修改后再发布 |
| 日志 | 每次登录和发布的结果都有记录。发布失败时会自动截图，保存在数据目录的 `screenshots/` 里 |

## 安装与启动（macOS / Windows / Linux）

**最简单：** 安装好 Python 3.10+ 后，macOS 双击 `start.command`，Windows 双击 `start.bat`。第一次运行会自动安装依赖，之后会打开浏览器。（macOS 如果提示无法打开，就右键 `start.command` 选择“打开”。）

手动安装：

需要 Python 3.10 或更高版本。

```bash
cd xhs-studio
python3 -m venv .venv
source .venv/bin/activate          # Windows: .venv\Scripts\activate
pip install -r requirements.txt
python -m playwright install chromium
python run.py                      # 自动打开 http://127.0.0.1:8765
```

如果要使用 AI 文案功能，启动前先设置 API Key：`export ANTHROPIC_API_KEY=sk-ant-...`

## 使用流程

1. 在 **账号** 页添加账号，点击 **扫码登录**，在弹出的窗口里用 App 扫码。
2. 在 **内容** 页新建笔记，上传图片，填写文案（也可以先用 AI 生成草稿）。
3. 点 **立即发布**，或者选好时间后点 **定时发布**。
4. 发布时程序要保持运行，电脑不能休眠。发布进度和结果可以在列表和 **日志** 页查看。

## 配置（环境变量）

| 变量 | 默认值 | 说明 |
| --- | --- | --- |
| `XHS_DATA_DIR` | `~/.xhs-studio` | 数据目录，存放数据库、图片、登录态和截图 |
| `XHS_MIN_INTERVAL_MINUTES` | `30` | 同一账号两次发布的最小间隔（分钟） |
| `XHS_DAILY_LIMIT` | `3` | 同一账号 24 小时内的发布上限 |
| `XHS_HEADLESS` | `0` | 设为 `1` 时发布过程不显示浏览器窗口（默认显示，方便遇到验证码时手动处理） |
| `XHS_BROWSER_PATH` | 空 | 指定浏览器路径，例如本机 Chrome：`/Applications/Google Chrome.app/Contents/MacOS/Google Chrome` |
| `XHS_AI_MODEL` | `claude-opus-5-5` | AI 文案使用的模型 |
| `XHS_LOGIN_TIMEOUT` | `180` | 扫码登录的等待时间（秒） |

## 网页改版后怎么修

小红书网页改版后，页面元素的选择器可能会失效。这时在数据目录下新建 `selectors.json`，只写需要覆盖的字段即可，**不需要改代码，也不需要重启**：

```json
{
  "title_input": "input[placeholder*=\"标题\"]",
  "body_editor": "div.tiptap.ProseMirror"
}
```

全部可覆盖的字段见 `xhs_studio/selectors.py`。排查问题时可以看失败截图，也可以开着浏览器窗口（`XHS_HEADLESS=0`）观察发布过程卡在哪一步。

## 合规说明（请务必阅读）

- 本工具用于运营**你自己的**账号，作用相当于替你在创作平台上点按钮。请遵守《小红书社区规范》和平台用户协议。平台不鼓励自动化操作，账号存在被限流或处罚的风险，需要自行承担。
- 刻意**没有**做这些功能：批量注册账号、刷赞、刷评论、自动关注或私信、规避平台风控检测。这些行为违反平台规则，也容易导致封号。
- AI 生成的内容发布前一定要人工核对。如果笔记含有 AI 生成的内容，请按平台要求勾选声明。
- 服务只监听 `127.0.0.1`，不要暴露到公网：它能以你的身份发帖。

## 开发

```bash
pip install -r requirements-dev.txt
pytest -q
```

代码结构：`app.py`（API）、`service.py`（发布流程和定时调度）、`browser.py`（Playwright 自动化）、`selectors.py`（页面选择器）、`ai.py`（Claude 文案）、`db.py`（SQLite）、`static/index.html`（前端）。
