import os
import sys
import llm
from email import message_from_bytes
from email.message import Message

LLM_MODEL = os.getenv("LLM_MODEL", "openrouter/google/gemini-2.5-flash")
# Max tokens to send (Gemini 2.5 Flash supports 1,048,576 tokens)
# Observed ratio from production: ~1.4 chars per token for email content
# Target ~800K tokens to leave headroom for prompt overhead (250K token buffer)
MAX_CHARS = int(os.getenv("LLM_MAX_CHARS", "1120000"))  # ~800k tokens * 1.4 chars/token

def _extract_text_content(msg: Message) -> str:
    """Extract only text content from email, excluding attachments."""
    text_parts: list[str] = []

    if msg.is_multipart():
        for part in msg.walk():
            content_type = part.get_content_type()
            content_disposition = part.get("Content-Disposition", "")

            # Skip attachments (identified by Content-Disposition: attachment)
            if "attachment" in content_disposition:
                continue

            # Only extract text/plain and text/html parts
            if content_type in ("text/plain", "text/html"):
                try:
                    payload = part.get_payload(decode=True)
                    if payload:
                        charset = part.get_content_charset() or "utf-8"
                        text_parts.append(payload.decode(charset, errors="replace"))
                except Exception:
                    pass
    else:
        # Non-multipart message - just get the payload
        try:
            payload = msg.get_payload(decode=True)
            if payload:
                charset = msg.get_content_charset() or "utf-8"
                text_parts.append(payload.decode(charset, errors="replace"))
        except Exception:
            pass

    return "\n\n".join(text_parts)

def classify_message(headers_text: str, raw_email: bytes) -> str:
    """
    Classify email using text content only (excluding attachments)
    Uses llm package which supports multiple providers
    """
    subject = ""
    for line in headers_text.splitlines():
        if line.lower().startswith("subject:"):
            subject = line.split(":", 1)[1].strip()
            break

    # Parse email and extract only text content (no attachments)
    try:
        msg = message_from_bytes(raw_email)
        full_content = _extract_text_content(msg)
    except Exception:
        # Fallback to raw decoding if parsing fails
        full_content = raw_email[:MAX_CHARS].decode("utf-8", errors="replace")

    # Truncate to MAX_CHARS after extraction
    full_content = full_content[:MAX_CHARS]

    prompt = (
        "You are an email triage classifier. "
        "Return exactly one of: spam, promotional, or normal. "
        "Rules: newsletters/ads/sales = promotional; political/phishing/scam/junk = spam; valid personal or work = normal.\n"
        f"Subject: {subject}\n"
        "Headers:\n"
        f"{headers_text}\n"
        "Full email body:\n"
        f"{full_content}\n"
        "Answer with only the single label."
    )

    try:
        model = llm.get_model(LLM_MODEL)
        response = model.prompt(
            prompt,
            system="Classify emails for triage using minimal tokens.",
            temperature=0.0,
        )
        out = response.text().strip().lower()
    except llm.errors.NeedsKeyException as e:
        print(f"\nERROR: {e}", file=sys.stderr)
        print("\nTo set up your API key, run:", file=sys.stderr)
        print("  llm keys set openrouter", file=sys.stderr)
        print("\nOr set the environment variable:", file=sys.stderr)
        print("  export LLM_OPENROUTER_KEY=your-key-here", file=sys.stderr)
        print("\nGet your API key from: https://openrouter.ai/keys", file=sys.stderr)
        sys.exit(1)
    except Exception as e:
        print(f"\nERROR: Failed to classify email: {e}", file=sys.stderr)
        sys.exit(1)

    if "spam" in out:
        return "spam"
    if "promo" in out:
        return "promotional"
    return "normal"
