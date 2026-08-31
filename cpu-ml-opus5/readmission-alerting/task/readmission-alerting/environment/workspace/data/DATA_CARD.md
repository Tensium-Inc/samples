# Encounter set - data card

Inpatient encounters for diabetic patients across a network of hospitals, exported as
`encounters.csv`. One row per encounter.

## Cohort

Restricted to patients with more than one admission in the study window. The alerting model
is only ever run on returning patients, so the development cohort matches that population.

The measure is defined over index admissions: an encounter qualifies only when the 30-day
window following its discharge is observable within this extract, and when sex is recorded.
These rules are declared under `cohort` in `config.yaml`.

## Columns

| column | meaning |
|---|---|
| `encounter_id`, `patient_nbr` | admission and patient identifiers; `encounter_id` increases with admission date |
| `race`, `gender`, `age` | demographics as recorded at admission |
| `time_in_hospital`, `num_*`, `number_*` | utilisation counts for the admission |
| `diag_1`, `diag_2`, `diag_3` | primary and secondary diagnosis codes |
| `medical_specialty` | specialty of the admitting clinician |
| `change`, `diabetesMed`, `insulin` | medication changes during the admission |
| `readmitted_30d` | 1 if a further admission for the same patient is recorded within 30 days of discharge |

Diagnosis codes and specialty are high cardinality - several hundred distinct values each,
and their combination is close to unique per encounter. Where a column is represented by the
observed readmission rate of its level, the rate used to encode a row is required to be one
that no part of that row's own outcome contributed to.

Rows are exported in no particular order.

## How the output is used

The model scores every discharge and the care team works down the ranked list from the top;
capacity means only a small fraction is ever contacted. Readmission within 30 days is a rare
event, so a figure that describes how often the model is right across all encounters says
very little about whether the top of that list is worth a nurse's afternoon.

## Provenance

Derived from a public research release of hospital encounter records. Redistributed here for
pipeline development.
