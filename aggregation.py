import json
import os


def load_recipes():
    base_dir = os.path.dirname(os.path.abspath(__file__))
    path = os.path.join(base_dir, "data", "recipes.json")
    with open(path, encoding="utf-8") as f:
        return json.load(f)


def score_recipe(recipe, preference):
    """Score a recipe against a single preference profile dict."""
    score = 0
    dietary = preference.get("dietary", "omnivore")
    cuisine = preference.get("cuisine", "any")
    spice = preference.get("spice_level", "medium")  # mild / medium / hot

    # --- Hard dietary constraints ---
    if dietary == "vegan":
        if recipe["dietary"] == "vegan":
            score += 3
        else:
            score -= 10
    elif dietary == "vegetarian":
        if recipe["dietary"] in ["vegan", "vegetarian"]:
            score += 3
        else:
            score -= 8
    else:
        score += 1  # omnivore, no penalty

    # --- Cuisine preference ---
    if cuisine != "any" and recipe["cuisine"] == cuisine:
        score += 2

    # --- Spice preference ---
    # Recipe spice_level is a string: mild / medium / spicy
    # Participant preference from chat: mild / medium / hot
    recipe_spice = recipe.get("spice_level", "mild")
    spice_norm = "spicy" if spice == "hot" else spice

    if spice_norm == "mild":
        if recipe_spice == "mild":
            score += 2
        elif recipe_spice == "medium":
            score += 0
        else:
            score -= 3
    elif spice_norm == "medium":
        if recipe_spice == "medium":
            score += 2
        elif recipe_spice == "mild":
            score += 1
        else:
            score -= 1
    elif spice_norm == "spicy":
        if recipe_spice == "spicy":
            score += 2
        elif recipe_spice == "medium":
            score += 1
        else:
            score -= 1

    return score


def additive_utilitarian(scores):
    return sum(scores)


def least_misery(scores):
    return min(scores)


def majority_voting(scores):
    """
    Counts how many group members have a positive score (score > 0).
    The recipe with the most 'votes' wins.
    Ties are broken by total score sum.
    """
    votes = sum(1 for s in scores if s > 0)
    return votes


def recommend(participant_pref, persona_prefs, strategy, n=3, exclude=None):
    """
    Generate top-n recipe recommendations.

    Parameters:
        participant_pref: dict with keys dietary, cuisine, spice_level
        persona_prefs:    list of 2 dicts (simulated personas)
        strategy:         'additive' | 'least_misery' | 'majority_voting'
        n:                number of recipes to return
        exclude:          list of recipe names already shown
    """
    if exclude is None:
        exclude = []

    recipes = load_recipes()
    recipes = [r for r in recipes if r["name"] not in exclude]

    all_prefs = [participant_pref] + persona_prefs
    scored = []

    for recipe in recipes:
        scores = [score_recipe(recipe, pref) for pref in all_prefs]

        if strategy == "additive":
            total = additive_utilitarian(scores)
        elif strategy == "least_misery":
            total = least_misery(scores)
        elif strategy == "majority_voting":
            # Primary sort: votes. Secondary sort: total sum to break ties.
            total = (majority_voting(scores), sum(scores))
        else:
            raise ValueError(f"Unknown strategy: {strategy}")

        scored.append((recipe, total, scores))

    # Sort descending — tuples compare element by element automatically
    scored.sort(key=lambda x: x[1], reverse=True)
    return scored[:n]