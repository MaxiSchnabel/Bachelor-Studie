from flask import (Flask, render_template, request,
                   session, redirect, url_for, Response)
from aggregation import recommend
from database import (init_db, create_participant, save_preferences,
                      save_response, save_demographics,
                      export_csv, get_stats, STRATEGY_ORDERS)
import os

app = Flask(__name__)
app.secret_key = os.environ.get("SECRET_KEY", "dev-secret-regensburg-2024")

# Admin password — set via environment variable in production
ADMIN_PASSWORD = os.environ.get("ADMIN_PASSWORD", "admin1234")

# Simulated personas (fixed across all sessions)
PERSONAS = [
    {
        "name": "Alex",
        "dietary":     "omnivore",
        "cuisine":     "indian",
        "spice_level": "hot",
        "description": "Loves Indian food and very spicy dishes."
    },
    {
        "name": "Sam",
        "dietary":     "omnivore",
        "cuisine":     "italian",
        "spice_level": "mild",
        "description": "Prefers Italian cuisine and mild flavours."
    },
]


@app.before_request
def setup():
    init_db()


# ── Admin ────────────────────────────────────────────────────────────────────

@app.route("/admin", methods=["GET", "POST"])
def admin():
    """Password-protected admin dashboard."""
    if request.method == "POST":
        if request.form.get("password") == ADMIN_PASSWORD:
            session["admin"] = True
            return redirect(url_for("admin"))
        return render_template("admin.html", error="Wrong password", stats=None)

    if not session.get("admin"):
        return render_template("admin.html", error=None, stats=None)

    stats = get_stats()
    return render_template("admin.html", error=None, stats=stats)


@app.route("/admin/export")
def admin_export():
    if not session.get("admin"):
        return redirect(url_for("admin"))
    csv_data = export_csv()
    return Response(
        csv_data,
        mimetype="text/csv",
        headers={"Content-Disposition": "attachment; filename=study_data.csv"}
    )


@app.route("/admin/logout")
def admin_logout():
    session.pop("admin", None)
    return redirect(url_for("admin"))


# ── Study flow ────────────────────────────────────────────────────────────────

@app.route("/")
def index():
    """Entry point — create participant, start session."""
    pid, order_index = create_participant()
    session.clear()
    session["pid"]            = pid
    session["order_index"]    = order_index
    session["strategy_order"] = STRATEGY_ORDERS[order_index]
    session["current_round"]  = 0
    session["shown_recipes"]  = []
    return redirect(url_for("consent"))


@app.route("/consent")
def consent():
    if "pid" not in session:
        return redirect(url_for("index"))
    return render_template("consent.html")


@app.route("/dialogue")
def dialogue():
    if "pid" not in session:
        return redirect(url_for("index"))
    return render_template("dialogue.html")


@app.route("/api/chat", methods=["POST"])
def chat():
    """Slot-filling dialogue — saves final preferences to DB."""
    from flask import jsonify
    data   = request.get_json()
    step   = data.get("step")
    answer = data.get("answer", "").strip().lower()

    slot_map = {
        "dietary": "dietary",
        "cuisine": "cuisine",
        "spice":   "spice_level"
    }

    # Store in session
    pref = session.get("participant_pref", {})
    if step in slot_map:
        pref[slot_map[step]] = answer
    session["participant_pref"] = pref

    steps = {
        "dietary": {
            "next_step": "cuisine",
            "question":  "Which type of cuisine do you prefer today?",
            "options":   ["Asian","Italian","Mexican","Indian","Mediterranean","American","No preference"],
            "values":    ["asian","italian","mexican","indian","mediterranean","american","any"],
            "done": False
        },
        "cuisine": {
            "next_step": "spice",
            "question":  "How spicy do you like your food?",
            "options":   ["Mild 🌿","Medium 🌶","Hot 🔥"],
            "values":    ["mild","medium","hot"],
            "done": False
        },
        "spice": {"done": True}
    }

    if step not in steps:
        return jsonify({"error": "invalid step"}), 400

    result = steps[step]

    # On final step, persist preferences to DB
    if result["done"]:
        save_preferences(
            session["pid"],
            pref.get("dietary", "omnivore"),
            pref.get("cuisine", "any"),
            pref.get("spice_level", "medium")
        )

    return jsonify(result)


@app.route("/personas")
def personas():
    if "pid" not in session:
        return redirect(url_for("index"))
    return render_template("personas.html", personas=PERSONAS)


@app.route("/recommend")
def get_recommendation():
    if "pid" not in session:
        return redirect(url_for("index"))

    round_num = session.get("current_round", 0)
    if round_num >= 3:
        return redirect(url_for("done"))

    strategy         = session["strategy_order"][round_num]
    exclude          = session.get("shown_recipes", [])
    participant_pref = session.get("participant_pref", {})

    results = recommend(participant_pref, PERSONAS, strategy, n=3, exclude=exclude)
    recipes = [r for r, score, scores in results]

    shown = session.get("shown_recipes", [])
    shown += [r["name"] for r in recipes]
    session["shown_recipes"]   = shown
    session["current_round"]   = round_num + 1
    session["current_recipes"] = [r["name"] for r in recipes]

    return render_template(
        "recommendation.html",
        recipes=recipes,
        round_num=round_num + 1,
        has_next=(round_num + 1 < 3)
    )


@app.route("/questionnaire", methods=["GET", "POST"])
def questionnaire():
    if "pid" not in session:
        return redirect(url_for("index"))

    if request.method == "POST":
        round_num = session.get("current_round", 1)
        strategy  = session["strategy_order"][round_num - 1]

        save_response(
            participant_id=session["pid"],
            round_num=round_num,
            strategy=strategy,
            recipes_shown=" | ".join(session.get("current_recipes", [])),
            form_data=request.form
        )

        if round_num >= 3:
            return redirect(url_for("demographics"))
        return redirect(url_for("get_recommendation"))

    round_num = session.get("current_round", 1)
    return render_template("questionnaire.html", round_num=round_num)


@app.route("/demographics", methods=["GET", "POST"])
def demographics():
    if "pid" not in session:
        return redirect(url_for("index"))

    if request.method == "POST":
        save_demographics(session["pid"], request.form)
        session.clear()
        return redirect(url_for("done"))

    return render_template("demographics.html")


@app.route("/done")
def done():
    return render_template("done.html")


if __name__ == "__main__":
    app.run(debug=True)
