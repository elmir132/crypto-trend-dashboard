import os

from flask import Flask, Response, jsonify, render_template, request

import history as history_module
import market

CSP = ("default-src 'none'; script-src 'self'; style-src 'self'; img-src 'self' data:; "
       "connect-src 'self'; base-uri 'none'; form-action 'none'; frame-ancestors 'none'")


def create_app(history_store=None):
    market.configure()
    store = history_store if history_store is not None else history_module.from_env()
    app = Flask(__name__)

    def summary_or_none():
        data = market.build_summary()
        if store is not None:
            try:
                store.record(data)
            except Exception as exc:  # history is a bonus: never break the page because of it
                app.logger.warning("could not save history: %s", exc)
                data["errors"].append("History could not be saved (see the server log).")
        return data

    @app.after_request
    def security_headers(response):
        response.headers["X-Content-Type-Options"] = "nosniff"
        response.headers["Referrer-Policy"] = "no-referrer"
        response.headers["Content-Security-Policy"] = CSP
        if request.path.startswith("/api/"):
            response.headers["Cache-Control"] = "no-store"
        return response

    @app.route("/")
    def index():
        return render_template("index.html", static_base="/static/", demo=False)

    @app.route("/api/summary")
    def summary():
        data = summary_or_none()
        status = 200 if data.pop("ok") else 502
        return jsonify(data), status

    @app.route("/api/post")
    def post():
        fmt = request.args.get("format", "text")
        if fmt not in ("text", "markdown"):
            return jsonify(error="format must be text or markdown"), 400
        data = summary_or_none()
        body = data["post_markdown"] if fmt == "markdown" else data["post"]
        mimetype = "text/markdown" if fmt == "markdown" else "text/plain"
        return Response(body + "\n", status=200 if data["ok"] else 502, mimetype=mimetype, headers={"Content-Type": f"{mimetype}; charset=utf-8"})

    @app.route("/api/summary.csv")
    def summary_csv():
        data = summary_or_none()
        body = market.build_csv(data["headline"], data["sector_details"])
        return Response(body, status=200 if data["ok"] else 502, headers={
            "Content-Type": "text/csv; charset=utf-8",
            "Content-Disposition": 'attachment; filename="crypto-trend-summary.csv"',
        })

    @app.route("/api/history")
    def history():
        if store is None:
            return jsonify(enabled=False, points=[])
        try:
            hours = min(720, max(1, int(request.args.get("hours", "24"))))
        except ValueError:
            return jsonify(error="hours must be a whole number"), 400
        return jsonify(enabled=True, hours=hours, points=store.series(hours=hours))

    @app.route("/healthz")
    def healthz():
        return jsonify(ok=True)

    return app


app = create_app()

if __name__ == "__main__":
    app.run(port=int(os.environ.get("PORT", "5000")), debug=False)
