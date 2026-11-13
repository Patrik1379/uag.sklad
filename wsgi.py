from app import create_app

app = create_app()

if __name__ == "__main__":
    from app.db import init_db

    init_db()
    app.run(host="0.0.0.0", port=8000, debug=True)
