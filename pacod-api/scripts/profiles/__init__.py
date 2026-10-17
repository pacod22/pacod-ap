"""Profile specs consumed by ``scripts.seed_profile``.

One module per agent: ``SPEC = ProfileSpec(...)`` holding the descriptive
fields, prompt overrides and seeding switches. The generic seeder does the
work; these files hold only what differs between agents.
"""
