"""Reviews & Feedback: sample reviews and a feedback form, a page in the top navigation bar.

It embeds the Invoice Hub's feedback page (the hub's static site, see apps/invoice_hub/component.py).
The page needs no sign-in; anything typed is stored in this browser only.
"""
from apps.invoice_hub.component import invoice_hub

# The component always loads index.html; page= opens the feedback page instead
# (see site/streamlit-bridge.js).
invoice_hub(page="feedback.html", key="feedback")
