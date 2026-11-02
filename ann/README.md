# Financial-statement page classifier

A small neural network that reads the text of one page of an Indian bank or NBFC
**annual report** and says whether it is the **profit & loss statement**, the
**balance sheet**, or **something else**. Annual reports run to 250–700 pages;
credit review needs about four of them. The classifier is meant to (a) point the
extraction pipeline at the right pages and (b) act as a pre-flight check that
rejects uploads containing no financial statements.

It is **not integrated into the app yet**. The app still uses the keyword logic in
`services/reconstruction/page_index.py`.

| Class | Meaning |
|---|---|
| `pnl` | Statement of profit and loss / profit and loss account, including its continuation page |
| `balance_sheet` | Balance sheet / statement of financial position, including its continuation page |
| `other` | Everything else: narrative, notes, schedules, auditor's report, cash flow, … |

Scope: annual reports only. Investor presentations are out of scope and keep using
the keyword logic.

## Architecture

1. **TF-IDF features** (`scikit-learn`): each page's text becomes a 5,000-dimensional
   vector, one weight per informative word (English stop words removed,
   sub-linear term frequency, words must appear on at least two pages).
2. **Keras MLP**:

   ```
   Input(5000) → Dense(256, relu) → Dropout(0.3)
               → Dense(128, relu) → Dropout(0.3)
               → Dense(3, softmax)          # pnl, balance_sheet, other
   ```

   1,313,539 parameters; loss `categorical_crossentropy`, optimiser `adam`.
   The prediction-only model file is 5.3 MB and runs on CPU.

**Class imbalance.** Statement pages are about 1% of all pages. Training uses
`class_weight="balanced"` (phase 1: `pnl` ≈ 66, `balance_sheet` ≈ 68,
`other` ≈ 0.34), so each class counts equally in the loss.

**Training procedure** (seed 42, TensorFlow deterministic ops: re-running gives
byte-identical metrics):

1. *Phase 1.* Train on 6 training companies; after every epoch, score the F1 of
   `pnl` and `balance_sheet` on 2 held-back **validation companies** (HDFC Bank,
   Bajaj group). Keep the best epoch (patience 8, max 40). Best: **epoch 8**, which
   is also where validation loss is lowest.
2. *Phase 2.* Retrain from scratch on all 8 training companies for 8 epochs
   (statement pages are scarce, so the validation pages are worth adding back).
3. Evaluate once on the 5 held-out **test companies**.

## Dataset

25 distinct annual reports (after removing byte-identical copies from 360 PDFs, of
which 117 investor presentations were skipped by file name), from 15 companies
grouped into 13 company groups. A parent and subsidiary whose reports overlap or
share a layout form one group: Kotak Mahindra Bank + Kotak Mahindra Investments,
and Bajaj Finance + Bajaj Housing Finance.

**10,548 pages**: 54 `pnl` (0.5%), 52 `balance_sheet` (0.5%), 10,442 `other` (99.0%).

### Split by company, not by page

Reports from the same company reuse their layout year after year. Every page of a
company group therefore sits entirely in train or entirely in test, so the test
score measures generalisation to companies the model has never seen.

| Split | Company | Reports | `pnl` | `balance_sheet` | `other` |
|---|---|---|---|---|---|
| train | Axis Bank | 1 | 2 | 2 | 500 |
| train | Can Fin Homes | 1 | 1 | 1 | 354 |
| train | IndusInd Bank | 1 | 2 | 3 | 411 |
| train | Kotak group | 8 | 13 | 13 | 2,545 |
| train | L&T Finance | 3 | 9 | 9 | 1,932 |
| train | LIC Housing Finance | 3 | 8 | 6 | 1,161 |
| train (validation) | Bajaj group | 2 | 6 | 6 | 713 |
| train (validation) | HDFC Bank | 1 | 2 | 2 | 586 |
| **test** | ICICI Bank | 1 | 2 | 2 | 337 |
| **test** | Muthoot Finance | 1 | 2 | 2 | 418 |
| **test** | PNB Housing Finance | 1 | 2 | 2 | 421 |
| **test** | Shriram Finance | 1 | 3 | 2 | 551 |
| **test** | State Bank of India | 1 | 2 | 2 | 513 |
| | **Train total** (8 groups) | 20 | **43** | **42** | 8,202 |
| | **Test total** (5 companies) | 5 | **11** | **10** | 2,240 |

The test set mixes 2 banks (Banking Regulation Act Form A/B statements) and 3
NBFC / housing-finance companies (Ind AS statements). Several Kotak files overlap
in content, so the training set has about **70 unique** statement pages.

## How the labels were made

Hand-labelling 10,000+ pages was not practical, so labels were **bootstrapped from
rules** and **hand-verified on the test set**.

1. **Rules, all pages** (`prepare_dataset.py`). A page is a statement when it
   carries a statement title from the keyword lists in `page_index.py` (plus
   "statement of financial position" and the bank title "profit and loss
   account") near the top of the page **and** the line items a real statement
   always has:
   * P&L: "before tax" and "total income"/"total expenses" (Ind AS), or interest
     earned, interest expended and net profit (banks).
   * Balance sheet: the full set: total assets, total liabilities, equity share
     capital, other equity, cash and cash equivalents, borrowings (Ind AS), or the
     bank Form A equivalents. Requiring the full set stops notes such as
     "maturity analysis of assets and liabilities" from being labelled.
   * Extra rules catch statements split over two pages (each half carries only
     part of the line items) and pages whose title is an image.

   Every page the rules labelled as a statement was read during development: about
   94% precision for `pnl` (51 of 54) and 100% for `balance_sheet` (52 of 52), up
   from 14–20% with title keywords alone.
2. **Hand verification, test set** (`make_review.py`). A reviewer checked 61 test
   pages: every page labelled as a statement plus every `other` page most likely
   to be a miss (statement title with totals, most of a statement's line items,
   or next to a statement page). **Two labels were corrected**: Shriram p470, a
   discontinued-operations note, `pnl` → `other`; Shriram p390, the second page of
   the consolidated P&L, `other` → `pnl`. The verified labels are stored in
   `test_labels.csv` (file name, page, label; no page text). Pages not listed
   are `other`, and `make_split.py` always takes test labels from this file, so
   later rule changes cannot move the test set.

Training labels were **not** hand-checked; they include at least two known errors
(LIC income-tax notes labelled `pnl`).

## Results on the held-out test companies

From `model/metrics.txt` (also `model/confusion_matrix.png` and `notebook.ipynb`):

|  | precision | recall | F1 | support |
|---|---|---|---|---|
| `pnl` | 1.000 | 0.818 | 0.900 | 11 |
| `balance_sheet` | 0.889 | 0.800 | 0.842 | 10 |
| `other` | 0.998 | 1.000 | 0.999 | 2,240 |
| **Statement F1** (mean of `pnl` and `balance_sheet`) | | | **0.871** | |

Confusion matrix (rows = true, columns = predicted):

| | `pnl` | `balance_sheet` | `other` |
|---|---|---|---|
| **`pnl`** | 9 | 0 | 2 |
| **`balance_sheet`** | 0 | 8 | 2 |
| **`other`** | 0 | 1 | 2,239 |

In plain terms: on five companies it never saw, the model found **9 of 11 P&L pages
and 8 of 10 balance sheets**, and raised **one** false alarm in 2,240 other pages.

**Accuracy is 99.78%, and that number is not the result.** 99.07% of test pages are
`other`, so a model that never predicted a statement would score 99.07% accuracy
while finding nothing. Per-class precision and recall are what show whether
statements are found.

Errors: ICICI Bank p147 and p265 (balance sheets) and p266 (P&L), and Muthoot p303
(P&L), predicted `other`; Shriram p471 (note on assets and liabilities of
discontinued operations) predicted `balance_sheet`. These were deliberately left
unexamined so that the test set measures the model rather than shaping it.

Validation companies (phase 1 model): statement F1 0.895 (pnl 0.857, balance_sheet
0.933), no false alarms.

## Comparison with the keyword baseline, and why the baseline is biased

The rule labels (`auto_label`) scored against the same hand-verified test labels:

| Statement F1 on the test companies | `pnl` | `balance_sheet` | Mean |
|---|---|---|---|
| Neural network | 0.900 | 0.842 | **0.871** |
| Keyword + line-item rules | 0.909 | 1.000 | **0.955** |

**On these numbers the network does not beat the rules. The rules' score is also
biased upward**:

* The test labels were **produced by these same rules** and then reviewed. The rules
  can only be counted wrong where a reviewer corrected them (2 of 2,261 pages).
* A statement that both the rules and the reviewer missed counts against neither,
  while the network is scored against labels it had no part in making.
* The rules were refined while reading reports from some of these companies (the
  line-item and split-page fixes), so they are partly tuned to this data.

The honest reading: on unseen companies the network finds most statement pages with
almost no false alarms, but there is no evidence yet that it beats the rules. Its
value is as an independent second opinion and as a component that improves with
more data rather than more hand-written rules.

## Limitations

* **Small data.** About 70 unique training statement pages and 21 test statement
  pages. One test error moves recall by 5–10 points; the scores are indicative,
  not precise.
* **Fragile epoch choice.** Validation F1 jumps between epochs (0.66 for most epochs,
  0.90 at epoch 8). Validation loss agrees on epoch 8, but the choice rests on 16
  validation statement pages.
* **Bootstrapped training labels.** The network partly learns to imitate the rules,
  including their errors. Only the test set is hand-verified.
* **Narrow coverage.** 15 companies, mostly FY2023–FY2025, annual reports only. Text
  comes from PyMuPDF; pages whose statements are scanned images have no text and
  cannot be classified.
* **Deployment not done.** TensorFlow pulls in `protobuf` 7.x, which conflicts with
  `google-generativeai`'s pin (`protobuf<6`). `requirements.txt` has not been
  changed; this needs resolving before the model ships to Hugging Face.

## Future work

* **More annual reports** from more banks and NBFCs: the single biggest lever. Each
  report adds about four statement pages and a new layout.
* **Cross-validation by company** (rotate which companies are held out) to get error
  bars instead of one score.
* Hand-verify a sample of training labels.
* Integrate into `page_index.py` as an optional classifier with the keyword scorer as
  fallback, plus the pre-flight check (reject documents with no `pnl` /
  `balance_sheet` pages).

## Files

| Path | Meant for git | Contents |
|---|---|---|
| `prepare_dataset.py` | yes | Extract page text from the PDF folders, skip presentations and duplicates, auto-label |
| `make_split.py` | yes | Company mapping, train/test split by company, apply hand-verified test labels |
| `make_review.py` | yes | Build the hand-check sheet for the test set |
| `train.py` | yes | Train, evaluate, save model and metrics |
| `notebook.ipynb` | yes | Presentation walk-through with outputs (no page text) |
| `test_labels.csv` | yes | Hand-verified test labels (file name, page, label) |
| `model/page_classifier_slim.keras` | yes | Prediction-only model, 5.3 MB: **use this one for deployment** |
| `model/page_classifier.keras` | yes | Same model with optimizer state, 15.8 MB, to resume training |
| `model/tfidf.pkl` | yes | Fitted TF-IDF vectorizer (163 KB) |
| `model/metrics.txt`, `confusion_matrix.png`, `history.json` | yes | Results and training curves |
| `_data/` | **no** (gitignored) | Extracted page text, splits, review sheet |

The source PDFs live outside the repository and are never committed.

## How to reproduce

From the project root, in an environment with the app's requirements plus
`tensorflow`, `scikit-learn` and `matplotlib` (and `nbconvert`/`ipykernel` for the
notebook):

```bash
python ann/prepare_dataset.py --pdf-dir "C:\Users\neerd\Desktop\pdfs" --pdf-dir "C:\Users\neerd\Desktop\pdfs_extra"
python ann/make_split.py
python ann/train.py
jupyter nbconvert --to notebook --execute --inplace ann/notebook.ipynb
```

`make_split.py` reads `test_labels.csv`, so the test set stays as verified. To verify a
new test set, run `make_review.py`, fill in `_data/test_review.csv`, then
`make_split.py --save-test-labels`. The model output order is
`["pnl", "balance_sheet", "other"]` (`train.LABELS`).
