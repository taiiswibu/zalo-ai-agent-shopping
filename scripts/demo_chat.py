"""Offline interactive demo: no Zalo token / Azure / Gemini needed."""
from app.db import init_db, SessionLocal
from app.seed import load_demo
from app.agent import respond


def main():
    init_db()
    with SessionLocal() as db:
        print("Demo seed:", load_demo(db))
        print("Nhập /help, tai nghe dưới 1 triệu, bảo hành, /buy TAI-001 1 ... Gõ exit để thoát.")
        while True:
            try:
                question = input("Khách > ").strip()
            except (KeyboardInterrupt, EOFError):
                break
            if question.lower() in {"exit", "quit"}:
                break
            answer = respond(db, "local-demo", question)
            print("Bot >", answer.text, "\n")

if __name__ == "__main__":
    main()
