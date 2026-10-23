# Demo script (3–5 minutes)

The submitted video (`LaunchReady_demo.mp4`) is a captioned screen recording of this script. It was
recorded with `tools/record_demo.js` and finished with `tools/make_video.py`. If you want a
**voice-over**, read the lines below over the video; each line matches one caption. For the campus
round, the same order works as a live demo.

| # | On screen | Say |
|---|---|---|
| 1 | Title | "LaunchReady answers one question before print: is this artwork ready? AI reads, code checks, people decide." |
| 2 | Sample list | "These are Esko's sample files: the Dancing Fish granola carton, the US content sheet, and a 31-rule US rulebook from FDA 21 CFR 101 plus company standards. You can upload any artwork, rulebook or content sheet instead." |
| 3 | Checking | "It reads the PDF's text layer exactly: words, fonts and positions. It also finds the dieline panels and the front panel, decodes the barcode, and spots text hidden under other artwork." |
| 4 | Verdict | "Not ready to print: 18 definite problems, 4 for a person, 7 passes, and 2 rules that apply later." |
| 5 | U16 vegan | "This one needs two sources at once. The pack says 'Suitable for Vegans' and carries a vegan mark, but honey is in the ingredient list. The finding quotes the rule, explains why it matters, and shows it was decided by code." |
| 6 | U14, U06 | "'No added sugar' with honey and coconut sugar. Tree nuts in the ingredients, but only 'may contain' wording and no 'Contains' statement." |
| 7 | U10, U13, U02 | "The calories don't add up from the macros. The carbohydrate differs from the content sheet. The net weight is only on the back panel." |
| 8 | U27, U15 (review) | "Not everything is black and white. Hidden text in the print file goes to review. Zero grams of added sugars with honey is possible under rounding rules, so a person confirms it. That's how we avoid false alarms." |
| 9 | Content sheet issues | "The content sheet itself is checked too. Its sugars exceed its carbohydrate." |
| 10 | Ask | "You can ask about any finding. With an API key, the AI answers only from that finding's evidence." |
| 11 | Rules and date | "Rules have effective dates. Checked as of January 2027, a new company QR rule applies. Proposed FDA rules show as coming." |
| 12 | How it was checked, report | "Everything is traceable, and the report is ready to send to the agency." |
| 13 | Phone photo | "A phone photo of a proof catches all nine planted errors. What a photo can't prove goes to a person." |
| 14 | Evaluation | "Across 84 test artworks with known errors, every error was flagged with zero false alarms." |
| 15 | Architecture | "AI reads, code checks, people decide." |

## Switching AI assist on for a live demo

1. Copy `.env.example` to `.env` and add `GEMINI_API_KEY=<your key>`.
2. Run `python tools/check_ai.py`. It should print `AI assist works …`.
3. Restart the app. The header shows **AI assist on**, the judgment rule (U28) gets an AI assessment,
   and the "Ask" box answers in free text. The **How it was checked** tab lists every AI action.
4. To show the rule compiler, upload any regulation text as a PDF or Word file in the **Rulebook**
   field. The **Rules** tab then shows "compiled by AI (quote verified)".

## Re-recording the video

```bash
uvicorn app.main:app --port 8000 &
node tools/record_demo.js .cache/video          # needs Node + Playwright (npm i playwright)
python tools/make_video.py .cache/video LaunchReady_demo.mp4
```
