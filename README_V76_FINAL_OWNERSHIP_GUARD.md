# v76 — Final ownership guard

This release converts website verification from name matching into a fail-closed ownership decision.

Automatic verification requires two independent dimensions:

1. **Entity identity:** legal/public/project identity supported by source-record fields and page content.
2. **Website control:** NGO-bearing domain or hosted slug, page self-identification, and—when the selected URL is deep—independent confirmation from the site root.

Third-party pages are retained only in the audit as carrier evidence. They never enter the Avika input. Historical mismatch labels are not required: every uploaded URL is reclassified from its current URL, fetched page and site-root evidence.


## Final export guard

Verified results are checked again at checkpoint time. Deep URLs must carry an independent site-root confirmation. The Avika input is then rebuilt from the completed results rather than trusted from incremental state. Any result that fails this final check is downgraded to manual review and placed in the retry queue.
