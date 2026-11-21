"""Handle ordinary salutations without an expensive embedding or LLM call."""
import re

def greeting_reply(question: str) -> str | None:
    normalized = re.sub(r"[!?.\s]+", " ", question.strip().casefold()).strip()
    if normalized in {"hi", "hello", "hey", "hii", "hiii", "good morning", "good afternoon", "good evening", "namaste"}:
        return "Hi! 👋 How can I help you with IIT Indore today? You can ask me about academics, placements, campus services, or official documents."
    if normalized in {"thanks", "thank you", "thankyou", "thx"}:
        return "You're welcome! What else would you like to know?"
    if normalized in {"bye", "goodbye", "see you"}:
        return "Goodbye! Have a great day."
    return None
