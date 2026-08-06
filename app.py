from flask import (Flask, render_template, request,
                   session, redirect, url_for, Response)
from aggregation import recommend
from database import (init_db, create_participant, save_preferences, is_postgres,
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
        "spice_level": "spicy",
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
    try:
        init_db()
    except Exception as e:
        app.logger.error(f"init_db failed: {e}")


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
@app.route("/admin/export/<token>")
def admin_export(token=None):
    # Allow export via token (no session needed) or via session
    valid_token = token and token == ADMIN_PASSWORD
    if not session.get("admin") and not valid_token:
        return redirect(url_for("admin"))
    try:
        csv_data = export_csv()
        return Response(
            csv_data,
            mimetype="text/csv",
            headers={"Content-Disposition": "attachment; filename=study_data.csv"}
        )
    except Exception as e:
        return f"Export error: {e}", 500


@app.route("/admin/check-pw")
def check_pw():
    """Temporary debug route — remove after fixing password."""
    import os
    pw = os.environ.get("ADMIN_PASSWORD", "NOT SET")
    return f"ADMIN_PASSWORD is: {pw}"


@app.route("/admin/migrate")
def admin_migrate():
    """Run database migrations — add missing columns."""
    if not session.get("admin"):
        return redirect(url_for("admin"))
    from database import get_db, is_postgres
    conn = get_db()
    results = []
    try:
        cur = conn.cursor()
        if is_postgres():
            cur.execute("ALTER TABLE demographics ADD COLUMN IF NOT EXISTS matrikelnummer TEXT")
        else:
            try:
                cur.execute("ALTER TABLE demographics ADD COLUMN matrikelnummer TEXT")
            except Exception:
                pass
        conn.commit()
        results.append("matrikelnummer column: OK")
    except Exception as e:
        results.append(f"Error: {e}")
    finally:
        conn.close()
    return "<br>".join(results) + "<br><a href=/admin>Back to admin</a>"


@app.route("/admin/logout")
def admin_logout():
    session.pop("admin", None)
    return redirect(url_for("admin"))


@app.route("/admin/reset-db")
def admin_reset_db():
    """Delete and recreate the local SQLite database (only works without PostgreSQL)."""
    if not session.get("admin"):
        return redirect(url_for("admin"))
    if not is_postgres():
        import os
        from database import SQLITE_PATH
        try:
            os.remove(SQLITE_PATH)
        except FileNotFoundError:
            pass
        init_db()
        return "Database reset successfully. <a href=/admin>Back to admin</a>"
    return "Using PostgreSQL — no reset needed. <a href=/admin>Back to admin</a>"


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


NEGATIONS = ["not", "don't", "dont", "no", "never", "without", "can't handle",
             "cant handle", "not really", "not good with", "not great with",
             "not a fan", "avoid", "hate", "dislike"]


def has_negation(text, keyword):
    """Check if a keyword appears near a negation word."""
    t = text.lower()
    if keyword not in t:
        return False
    idx = t.index(keyword)
    # Look at the 4 words before the keyword
    preceding = t[:idx].split()[-4:]
    return any(neg in " ".join(preceding) for neg in NEGATIONS)


def extract_preferences(text, current_pref=None):
    """
    Extract dietary, cuisine and spice_level from free text.
    Handles negations, corrections, and ambiguous phrasing.
    Returns (found_dict, corrected_slots).
    corrected_slots = list of slot names that were changed from a previous value.
    """
    if current_pref is None:
        current_pref = {}
    t = text.lower()
    found = {}
    corrected = []

    # ── Correction detection ──────────────────────────────────────────────────
    correction_triggers = ["actually", "wait", "sorry", "i meant", "i mean",
                           "correction", "no wait", "not that", "change that",
                           "i said", "instead"]
    is_correction = any(trigger in t for trigger in correction_triggers)

    # ── Dietary ───────────────────────────────────────────────────────────────
    if any(w in t for w in ["vegan", "plant-based", "plant based", "no animal products"]):
        new_val = "vegan"
        if current_pref.get("dietary") and current_pref["dietary"] != new_val:
            corrected.append("dietary")
        found["dietary"] = new_val

    elif any(w in t for w in ["vegetarian", "veggie", "no meat", "without meat",
                               "don't eat meat", "dont eat meat"]):
        new_val = "vegetarian"
        if current_pref.get("dietary") and current_pref["dietary"] != new_val:
            corrected.append("dietary")
        found["dietary"] = new_val

    elif any(w in t for w in ["omnivore", "eat everything", "eat anything",
                               "no dietary restriction", "no restriction",
                               "no dietary preference"]):
        new_val = "omnivore"
        if current_pref.get("dietary") and current_pref["dietary"] != new_val:
            corrected.append("dietary")
        found["dietary"] = new_val

    elif any(w in t for w in ["meat", "chicken", "beef", "pork", "fish",
                               "seafood", "steak", "bacon"]):
        # Only infer omnivore from food items if not negated
        if not has_negation(t, next((w for w in ["meat","chicken","beef","pork",
                                                   "fish","seafood"] if w in t), "")):
            new_val = "omnivore"
            if current_pref.get("dietary") and current_pref["dietary"] != new_val:
                corrected.append("dietary")
            found["dietary"] = new_val

    # ── Cuisine ───────────────────────────────────────────────────────────────
    cuisine_map = {
        "asian":         ["asian", "chinese", "japanese", "thai", "korean",
                          "vietnamese", "sushi", "ramen", "dim sum", "wok", "stir fry"],
        "italian":       ["italian", "italy", "pasta", "pizza", "risotto",
                          "lasagna", "carbonara", "pesto", "tiramisu"],
        "mexican":       ["mexican", "mexico", "taco", "burrito", "enchilada",
                          "quesadilla", "guacamole", "salsa", "nacho"],
        "indian":        ["indian", "india", "curry", "tikka", "masala",
                          "dal", "biryani", "naan", "tandoori", "samosa"],
        "mediterranean": ["mediterranean", "greek", "turkish", "lebanese",
                          "falafel", "hummus", "kebab", "shawarma", "tzatziki"],
        "american":      ["american", "burger", "bbq", "barbecue", "wings",
                          "steak", "ribs", "hot dog", "fried chicken"],
        "indian":        ["indian", "india", "curry", "tikka", "masala",
                          "dal", "biryani", "naan", "tandoori", "samosa",
                          "paneer", "vindaloo", "korma", "chana", "saag"],
        "any":           ["no preference", "don't mind", "dont mind",
                          "no specific", "whatever", "any cuisine",
                          "any food", "anything works", "no strong preference"],
    }
    for cuisine, keywords in cuisine_map.items():
        if any(kw in t for kw in keywords):
            new_val = cuisine
            if current_pref.get("cuisine") and current_pref["cuisine"] != new_val:
                corrected.append("cuisine")
            found["cuisine"] = new_val
            break

    # ── Spice level — negation-aware ──────────────────────────────────────────
    spice_detected = None

    # Strong mild indicators (negations of spicy)
    mild_phrases = ["not spicy", "no spice", "no spicy", "not hot",
                    "not really good with spice", "not good with spice",
                    "not great with spice", "can't handle spice",
                    "cant handle spice", "not a fan of spicy",
                    "don't like spicy", "dont like spicy",
                    "avoid spicy", "hate spicy", "dislike spicy",
                    "sensitive to spice", "mild", "bland", "no heat",
                    "without spice", "not really into spicy",
                    "not into spicy", "not too spicy"]
    if any(p in t for p in mild_phrases):
        spice_detected = "mild"

    # Medium indicators
    elif any(p in t for p in ["medium spice", "medium heat", "moderate spice",
                                "a bit spicy", "a little spicy", "some spice",
                                "a little heat", "not too hot", "slightly spicy",
                                "medium", "moderate"]):
        spice_detected = "medium"

    # Hot indicators — only if no negation nearby
    elif any(p in t for p in ["very spicy", "extra spicy", "really spicy",
                                "love spicy", "i like spicy", "i love spicy",
                                "enjoy spicy", "spicy food", "hot food",
                                "hot and spicy", "extra hot", "very hot",
                                "bring the heat", "the spicier the better"]):
        spice_detected = "hot"

    # Generic "spicy" or "spice" — check for negation first
    elif "spic" in t or "heat" in t:
        if has_negation(t, "spic") or has_negation(t, "heat"):
            spice_detected = "mild"
        else:
            spice_detected = "hot"

    if spice_detected:
        if current_pref.get("spice_level") and current_pref["spice_level"] != spice_detected:
            corrected.append("spice_level")
        found["spice_level"] = spice_detected

    return found, corrected


# Follow-up questions per slot — add conversational depth
FOLLOWUP_QUESTIONS = {
    "dietary_vegan": "Great! Do you tend to prefer hearty, filling dishes or something lighter?",
    "dietary_vegetarian": "Got it! Are you more into comfort food like pasta and curries, or do you prefer fresh salads and grain bowls?",
    "dietary_omnivore": "No restrictions — nice! Do you tend to prefer meat-heavy dishes or do you enjoy a mix?",
    "cuisine_italian": "Italian it is! Are you more of a pasta person, or do you prefer pizza and risotto?",
    "cuisine_mexican": "Mexican sounds great! Do you go for tacos and burritos, or do you prefer something like enchiladas or casseroles?",
    "cuisine_mediterranean": "Mediterranean cuisine is a great choice! Do you prefer grilled dishes like kebabs, or lighter options like salads and dips?",
    "cuisine_asian": None,  # Handled separately by asian sub-cuisine question
    "cuisine_indian": "Indian food! Do you prefer rich curry dishes, or lighter options like dal and rice bowls?",
    "cuisine_american": "American classics! Are you a burger and BBQ fan, or do you prefer something like pasta or salads?",
    "cuisine_any": None,  # No follow-up needed for no preference
    "spice_hot": "You like it hot! Do you prefer a slow-building heat or an immediate kick?",
    "spice_medium": "Medium spice — a good balance! Do you lean more towards mild or spicy when you have the choice?",
    "spice_mild": "Mild it is! Do you avoid spice entirely, or is a little heat okay occasionally?",
}


def get_followup(slot, value, pref):
    """Return a follow-up question after a slot is filled, if appropriate."""
    # Don't ask follow-up if we already asked one for this slot
    if pref.get(f"followup_asked_{slot}"):
        return None
    # Don't ask follow-up if all slots already filled
    if all(k in pref for k in ["dietary", "cuisine", "spice_level"]):
        return None
    key = f"{slot}_{value}" if slot in ["dietary", "cuisine"] else f"spice_{value}"
    return FOLLOWUP_QUESTIONS.get(key)


def next_question(pref):
    """Return the next question based on missing slots."""
    if "dietary" not in pref:
        return ("Do you follow any particular dietary lifestyle? "
                "For example, are you omnivore, vegetarian, or vegan?")
    if "cuisine" not in pref:
        return ("Which type of cuisine do you prefer? "
                "For example Asian, Italian, Mexican, Indian, Mediterranean, or American?")
    if pref.get("cuisine") == "asian" and not pref.get("asian_refined"):
        return ("Within Asian cuisine, do you have a preference — "
                "for example Chinese, Japanese, Thai, or Korean?")
    if "spice_level" not in pref:
        return "How spicy do you like your food — mild, medium, or hot?"
    return None


def pref_state(pref):
    """Return structured pref state for the frontend summary panel."""
    return {
        "dietary":     pref.get("dietary"),
        "cuisine":     pref.get("cuisine"),
        "spice_level": pref.get("spice_level"),
    }


@app.route("/api/chat", methods=["POST"])
def chat():
    from flask import jsonify

    data         = request.get_json()
    user_message = data.get("message", "").strip()

    if not user_message:
        return jsonify({"error": "empty message"}), 400

    pref = session.get("participant_pref", {})

    # Extract with negation awareness and correction detection
    extracted, corrected = extract_preferences(user_message, pref)

    # Asian sub-cuisine refinement
    # Apply extracted values (corrections overwrite old values)
    pref.update(extracted)

    # Asian sub-cuisine refinement — checked AFTER update so cuisine=asian is set
    asian_keywords = ["chinese", "japanese", "thai", "korean", "vietnamese",
                      "cantonese", "szechuan", "sichuan", "sushi",
                      "ramen", "pho", "pad thai", "bibimbap"]
    if pref.get("cuisine") == "asian":
        if any(k in user_message.lower() for k in asian_keywords):
            pref["asian_refined"] = True
        elif extracted.get("cuisine") == "asian":
            pref["asian_refined"] = False

    session["participant_pref"] = pref

    all_filled = all(k in pref for k in ["dietary", "cuisine", "spice_level"])

    # ── Sub-cuisine detection ─────────────────────────────────────────────────
    asian_sub_map = {
        "chinese": "Chinese", "cantonese": "Cantonese",
        "szechuan": "Szechuan", "sichuan": "Szechuan",
        "japanese": "Japanese", "sushi": "Japanese",
        "ramen": "Japanese", "thai": "Thai", "pad thai": "Thai",
        "korean": "Korean", "bibimbap": "Korean",
        "vietnamese": "Vietnamese", "pho": "Vietnamese",
    }
    t_lower = user_message.lower()

    # Update asian_sub if cuisine is asian and user names a sub-type
    if pref.get("cuisine") == "asian" and pref.get("asian_refined"):
        for kw, label in asian_sub_map.items():
            if kw in t_lower:
                pref["asian_sub"] = label
                session["participant_pref"] = pref
                break

    # If cuisine changed away from asian, clear asian meta-data immediately
    if "cuisine" in corrected and pref.get("cuisine") != "asian":
        pref.pop("asian_sub", None)
        pref.pop("asian_refined", None)
        session["participant_pref"] = pref

    # ── Helper: build cuisine label from current pref ─────────────────────────
    def get_cuisine_label(p):
        if p["cuisine"] == "any":
            return "no specific cuisine preference"
        elif p["cuisine"] == "asian" and p.get("asian_sub"):
            return p["asian_sub"] + " cuisine"
        else:
            return p["cuisine"].capitalize() + " cuisine"

    # ── Build reply ───────────────────────────────────────────────────────────
    ack_parts = []

    # Acknowledge corrections — use updated cuisine label
    if corrected:
        slot_names = {"dietary": "dietary preference",
                      "cuisine": "cuisine preference",
                      "spice_level": "spice preference"}
        corrected_labels = [slot_names.get(s, s) for s in corrected]
        base = f"No problem, I've updated your {' and '.join(corrected_labels)}."
        # If cuisine was corrected, mention the new value explicitly
        if "cuisine" in corrected:
            base += f" You now prefer {get_cuisine_label(pref)}."
        ack_parts.append(base)

    # Acknowledge sub-cuisine answer
    if (pref.get("asian_refined") and pref.get("asian_sub")
            and "cuisine" not in corrected
            and any(kw in t_lower for kw in asian_sub_map)):
        ack_parts.append(f"{pref['asian_sub']} cuisine — noted.")
        newly = {k: v for k, v in extracted.items()
                 if k not in corrected and k != "cuisine"}
    else:
        newly = {k: v for k, v in extracted.items() if k not in corrected}

    ack_map = {
        "dietary":     {"vegan": "Got it, you're vegan.",
                        "vegetarian": "Got it, vegetarian.",
                        "omnivore": "Got it, no dietary restrictions."},
        "cuisine":     lambda v: ("Any cuisine works — noted."
                                  if v == "any"
                                  else f"{v.capitalize()} cuisine — noted."),
        "spice_level": {"mild":   "Noted, you prefer mild food.",
                        "medium": "Medium spice — noted.",
                        "hot":    "You like it hot — noted."},
    }
    for slot, val in newly.items():
        mapping = ack_map.get(slot)
        if isinstance(mapping, dict):
            ack_parts.append(mapping.get(val, ""))
        elif callable(mapping):
            ack_parts.append(mapping(val))

    if all_filled:
        save_preferences(
            session["pid"],
            pref["dietary"],
            pref["cuisine"],
            pref["spice_level"]
        )
        cuisine_label = get_cuisine_label(pref)
        confirm = (
            " ".join(p for p in ack_parts if p) + " " if ack_parts else ""
        ) + (
            f"I now have all your preferences: {pref['dietary']} diet, "
            f"{cuisine_label}, and {pref['spice_level']} spice. "
            f"I'll use this to find the best recipes for your group!"
        )
        return jsonify({"reply": confirm.strip(), "done": True,
                        "pref": pref_state(pref)})

    # Not done yet — show what we have and ask next question
    next_q = next_question(pref)

    if not extracted and not corrected and not pref.get("asian_refined"):
        hint = "I didn't quite catch that. "
        reply = hint + (next_q or "Could you tell me a bit more?")
    else:
        ack = " ".join(p for p in ack_parts if p)

        # Check if we should ask a follow-up question instead of moving to next slot
        followup = None
        if newly and not corrected:
            # Find the most recently filled slot
            for slot in ["dietary", "cuisine", "spice_level"]:
                if slot in newly:
                    followup = get_followup(slot, pref.get(slot, ""), pref)
                    if followup:
                        pref[f"followup_asked_{slot}"] = True
                        session["participant_pref"] = pref
                    break

        if followup:
            reply = f"{ack} {followup}".strip()
        elif next_q:
            reply = f"{ack} {next_q}".strip()
        else:
            reply = ack

    return jsonify({"reply": reply, "done": False,
                    "pref": pref_state(pref)})


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

    results = recommend(participant_pref, PERSONAS, strategy, n=1, exclude=exclude)
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

    # current_round is incremented AFTER recommendation is shown,
    # so the round just completed = current_round (already incremented)
    round_num = session.get("current_round", 1)

    if request.method == "POST":
        strategy_order = session.get("strategy_order", [])
        # Strategy for the round just completed (index = round_num - 1)
        strategy_index = round_num - 1
        if strategy_index < 0 or strategy_index >= len(strategy_order):
            return redirect(url_for("done"))
        strategy = strategy_order[strategy_index]

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

    return render_template("questionnaire.html", round_num=round_num)


@app.route("/demographics", methods=["GET", "POST"])
def demographics():
    if "pid" not in session:
        return redirect(url_for("index"))

    if request.method == "POST":
        try:
            save_demographics(session["pid"], request.form)
        except Exception as e:
            app.logger.error(f"save_demographics error: {e}")
            return f"Database error: {e}", 500
        session.clear()
        return redirect(url_for("done"))

    return render_template("demographics.html")


@app.route("/done")
def done():
    return render_template("done.html")


if __name__ == "__main__":
    app.run(debug=True)