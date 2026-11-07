# Contributing to ModemDiag

For command additions or corrections, include the affected command, exact module family,
a public reference and whether it was tested on hardware. Write short descriptions in your own words;
support can differ between models and firmware versions.

Only contribute material you have permission to publish. Keep vendor manuals, confidential material,
credentials, personal identifiers and private device configurations out of issues and pull requests.
Use simulated or redacted examples.

Describe behavior changes and run the relevant offline checks:

```bash
QT_QPA_PLATFORM=offscreen python3 -m unittest discover -v
```
