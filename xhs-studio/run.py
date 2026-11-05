"""启动 XHS Studio：python run.py [--host 127.0.0.1] [--port 8765]"""

import argparse
import webbrowser

import uvicorn

from xhs_studio.app import create_app


def main() -> None:
    parser = argparse.ArgumentParser(description="小红书运营工作台")
    parser.add_argument("--host", default="127.0.0.1", help="默认只监听本机，避免账号会话暴露到局域网")
    parser.add_argument("--port", type=int, default=8765)
    parser.add_argument("--no-browser", action="store_true", help="启动后不自动打开浏览器")
    args = parser.parse_args()

    url = f"http://{args.host}:{args.port}"
    print(f"XHS Studio 运行在 {url}")
    if not args.no_browser:
        webbrowser.open(url)
    uvicorn.run(create_app(), host=args.host, port=args.port, log_level="info")


if __name__ == "__main__":
    main()
