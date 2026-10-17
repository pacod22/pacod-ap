"""Newsletter worker — standalone always-on collect/send engine.

A separate, standalone-runnable service (sibling of improvement_loop/): it drains
the ``newsletter_run_requests`` queue (admin "Collect now"/"Send now" + the
calendar scheduler) one job at a time and executes it. A ``collect`` job crawls a
profile's newsletter sources, has the newsletter model classify each article's
relevance/importance/urgency, and stores the topics as ``pending`` items for
admin review. A ``send`` job compiles the profile's ``approved`` items into a
modern HTML email and sends it to the profile's subscribed users.

It reuses ``app.core.*`` / ``app.services.*`` as a library and talks to Postgres /
the LLM gateway / SMTP over the network — it imports nothing from the FastAPI app object,
so ``python -m newsletter_worker worker`` runs with the ``api`` container stopped.
"""
