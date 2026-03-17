import os

from flask import Flask, jsonify, render_template

import market


def create_app():
    app = Flask(__name__)

    @app.route("/")
    def index():
        return render_template("index.html")

    @app.route("/api/summary")
    def summary():
        errors = []
        try:
            prices = market.fetch_prices()
        except market.UpstreamError as exc:
            prices, errors = {}, [str(exc)]
        sectors, sector_errors = market.build_sectors()
        errors += sector_errors
        status = 200 if prices or any(sectors.values()) else 502
        return jsonify({
            "prices": prices,
            "sentiment": market.sentiment(prices),
            "sectors": sectors,
            "post": market.build_post(prices, sectors),
            "errors": errors,
        }), status

    @app.route("/healthz")
    def healthz():
        return jsonify(ok=True)

    return app


app = create_app()

if __name__ == "__main__":
    app.run(port=int(os.environ.get("PORT", "5000")), debug=False)
