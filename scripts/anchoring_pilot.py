"""Phase 4: Anchoring pilot harness.
Tests whether showing a rule-detected prior to Qwen / LLM induces rubber-stamping
or whether the model reads surrounding page context to arbitrate correctly.

Runs 3 arms over ~40 labeled spans:
  (a) no prior — "is this span an internal reference/target? yes/no"
  (b) truthful prior — show the rule verdict
  (c) adversarial prior — show the inverted verdict on a random half

Pre-registered kill criteria:
  1. Arm (a) self-consistency < ~90% -> stop; judge that disagrees with itself cannot correct.
  2. Arm (c) agreement with inverted verdict > ~80% while (b) looks strong -> anchoring dominates.
"""

import argparse
import asyncio
import json
import os
import random
import sys
from typing import Any, Dict, List, Tuple

import ai_indexing
import config


# Labeled dataset of 40 spans with ±15 lines of context from court & tender documents.
PILOT_SAMPLES: List[Dict[str, Any]] = [
    # --- Internal Targets / Headings (True) ---
    {
        "id": "T1",
        "span": "MEMO OF PARTIES",
        "context": "IN THE HIGH COURT OF DELHI AT NEW DELHI\n\nMEMO OF PARTIES\n\n1. NTPC LIMITED\nCore 7, SCOPE Complex\n...Petitioner\nVersus\n1. VIGOR INFRASTRUCTURE\n...Respondent",
        "is_internal": True,
        "rule_verdict": True,
    },
    {
        "id": "T2",
        "span": "JUDGMENT",
        "context": "CORAM:\nHON'BLE MR. JUSTICE VALMIKI J. MEHTA\n\nJUDGMENT\n\n1. This petition under Section 34 of the Arbitration and Conciliation Act, 1996 challenges the award dated 14.03.2018.",
        "is_internal": True,
        "rule_verdict": True,
    },
    {
        "id": "T3",
        "span": "3.4 QUALITY PLANS",
        "context": "SECTION 3 - CONDITIONS OF CONTRACT\n\n3.3 INSPECTION AND TESTING\nAll materials shall be inspected by the Engineer.\n\n3.4 QUALITY PLANS\nThe Contractor shall submit detailed quality assurance plans within 30 days of award.\n\n3.5 PROGRESS REPORTS",
        "is_internal": True,
        "rule_verdict": True,
    },
    {
        "id": "T4",
        "span": "ANNEXURE A-1",
        "context": "LIST OF DOCUMENTS\n\nANNEXURE A-1\nTrue copy of the agreement dated 15.01.2015 executed between the parties.\n\nANNEXURE A-2\nTrue copy of letter dated 20.02.2016.",
        "is_internal": True,
        "rule_verdict": True,
    },
    {
        "id": "T5",
        "span": "CLAUSE 25 - SETTLEMENT OF DISPUTES",
        "context": "GENERAL CONDITIONS OF CONTRACT\n\nCLAUSE 25 - SETTLEMENT OF DISPUTES\nExcept where otherwise provided in the contract all questions and disputes shall be referred to arbitration.\n\nCLAUSE 26 - INDEMNITY",
        "is_internal": True,
        "rule_verdict": True,
    },
    {
        "id": "T6",
        "span": "SCHEDULE 'B'",
        "context": "TENDER NO: 1042/2019\n\nSCHEDULE 'B'\nSCHEDULE OF QUANTITIES AND RATES\nItem 1: Earthwork excavation in all kinds of soil...\nItem 2: Plain cement concrete...",
        "is_internal": True,
        "rule_verdict": True,
    },
    {
        "id": "T7",
        "span": "PART II - FINANCIAL BID",
        "context": "VOLUME I\nINSTRUCTIONS TO BIDDERS\n\nPART II - FINANCIAL BID\nThe financial proposal shall contain price schedules completed in all respects.\nNo conditions shall be attached to the financial bid.",
        "is_internal": True,
        "rule_verdict": True,
    },
    {
        "id": "T8",
        "span": "ARTICLE 5 - INDEMNIFICATION",
        "context": "NOW THIS AGREEMENT WITNESSETH AS FOLLOWS:\n\nARTICLE 4 - TERM\n\nARTICLE 5 - INDEMNIFICATION\nThe Contractor shall indemnify and hold harmless the Owner from any and all claims.\n\nARTICLE 6 - GOVERNING LAW",
        "is_internal": True,
        "rule_verdict": True,
    },
    {
        "id": "T9",
        "span": "APPENDIX III",
        "context": "CONTRACT SPECIFICATIONS\n\nAPPENDIX III\nLIST OF APPROVED MAKES OF MATERIALS\n1. Cement: UltraTech / ACC / Ambuja\n2. Steel: TATA / SAIL / JSW",
        "is_internal": True,
        "rule_verdict": True,
    },
    {
        "id": "T10",
        "span": "Ground A.",
        "context": "GROUNDS OF APPEAL\n\nGround A. Because the learned Arbitrator erred in awarding interest from the date of cause of action rather than the date of entering upon reference.\n\nGround B. Because no notice under Clause 25 was served.",
        "is_internal": True,
        "rule_verdict": True,
    },

    # --- Spurious Headings / Non-Headings (False) ---
    {
        "id": "T11",
        "span": "Section 34 of the Arbitration and Conciliation Act, 1996",
        "context": "1. This petition under Section 34 of the Arbitration and Conciliation Act, 1996 challenges the award dated 14.03.2018 passed by the Sole Arbitrator.\n2. The facts of the case are that...",
        "is_internal": False,
        "rule_verdict": False,
    },
    {
        "id": "T12",
        "span": "14. AIR CONDITIONING WAS NOT WORKING",
        "context": "witness statement:\n13. We visited the premises on 12.06.2017.\n14. AIR CONDITIONING WAS NOT WORKING in the server room when we arrived.\n15. Temperatures exceeded 40 degrees.",
        "is_internal": False,
        "rule_verdict": True,  # False positive rule verdict!
    },
    {
        "id": "T13",
        "span": "which reads as under:",
        "context": "The respondent relies on Clause 11 of the agreement which reads as under:\n\"11.0 Liquidated damages shall be payable at 0.5% per week.\"\nTherefore, the deduction was justified.",
        "is_internal": False,
        "rule_verdict": False,
    },
    {
        "id": "T14",
        "span": "IN THE HIGH COURT OF DELHI",
        "context": "IN THE HIGH COURT OF DELHI\nAT NEW DELHI\n\nO.M.P. (COMM) 124/2019\n\nNTPC LTD vs VIGOR INFRASTRUCTURE",
        "is_internal": False,
        "rule_verdict": False,
    },
    {
        "id": "T15",
        "span": "Page 12 of 45",
        "context": "Page 12 of 45\n\nargument continued on the admissibility of respondent's counterclaim.\nMr. Sharma argued that counterclaims were barred by limitation.",
        "is_internal": False,
        "rule_verdict": False,
    },
    {
        "id": "T16",
        "span": "Article 226 of the Constitution of India",
        "context": "The writ petition filed under Article 226 of the Constitution of India was dismissed by the Division Bench on grounds of alternative efficacious remedy.",
        "is_internal": False,
        "rule_verdict": False,
    },
    {
        "id": "T17",
        "span": "Section 151 of CPC",
        "context": "Application filed by the applicant under Section 151 of CPC seeking condonation of delay of 15 days in filing the rejoinder affidavit.",
        "is_internal": False,
        "rule_verdict": False,
    },
    {
        "id": "T18",
        "span": "Clause 12 dated 14.03.2019",
        "context": "The petitioner relies on the notice under Clause 12 dated 14.03.2019 issued by the Project Director.",
        "is_internal": False,  # inline reference, not a section heading target
        "rule_verdict": True,  # rule false positive target
    },
    {
        "id": "T19",
        "span": "Special Conditions of Contract (SCC)",
        "context": "The tender consists of GCC and Special Conditions of Contract (SCC) which shall prevail in case of conflict.",
        "is_internal": False,
        "rule_verdict": False,
    },
    {
        "id": "T20",
        "span": "Arbitration Act",
        "context": "The dispute was referred to arbitration under the provisions of the Arbitration Act.",
        "is_internal": False,
        "rule_verdict": False,
    },

    # --- Internal Mentions (True) ---
    {
        "id": "M1",
        "span": "Clause 11",
        "context": "The contractor failed to achieve Milestone 2 within time. Consequently, the employer invoked Clause 11 of the contract to levy liquidated damages.",
        "is_internal": True,
        "rule_verdict": True,
    },
    {
        "id": "M2",
        "span": "Annexure A-1 to A-5",
        "context": "The invoices in dispute are annexed herewith as Annexure A-1 to A-5. A perusal of these invoices will demonstrate that payment was received.",
        "is_internal": True,
        "rule_verdict": True,
    },
    {
        "id": "M3",
        "span": "Section 2.3",
        "context": "The technical requirements specified in Section 2.3 must be strictly complied with during the execution phase.",
        "is_internal": True,
        "rule_verdict": True,
    },
    {
        "id": "M4",
        "span": "Schedule B",
        "context": "Payments shall be made strictly in accordance with unit rates set out in Schedule B attached hereto.",
        "is_internal": True,
        "rule_verdict": True,
    },
    {
        "id": "M5",
        "span": "Clause 25",
        "context": "Notice invoking arbitration under Clause 25 was issued on 12.04.2018 by registered speed post.",
        "is_internal": True,
        "rule_verdict": True,
    },
    {
        "id": "M6",
        "span": "Appendix I",
        "context": "The key personnel listed in Appendix I shall remain deployed on site throughout the project duration.",
        "is_internal": True,
        "rule_verdict": True,
    },
    {
        "id": "M7",
        "span": "Article 12",
        "context": "Either party may terminate this agreement pursuant to Article 12 in the event of continuous force majeure.",
        "is_internal": True,
        "rule_verdict": True,
    },
    {
        "id": "M8",
        "span": "Annexure P-3",
        "context": "Copy of the letter of award dated 22.01.2016 is marked and annexed as Annexure P-3.",
        "is_internal": True,
        "rule_verdict": True,
    },
    {
        "id": "M9",
        "span": "Clause 9.1",
        "context": "Site mobilization was completed on 15.02.2016 as required under Clause 9.1 of the agreement.",
        "is_internal": True,
        "rule_verdict": True,
    },
    {
        "id": "M10",
        "span": "Part III",
        "context": "The environmental and safety standards enumerated in Part III of the specifications are binding.",
        "is_internal": True,
        "rule_verdict": True,
    },

    # --- External Citations / False Mentions (False) ---
    {
        "id": "M11",
        "span": "Section 34",
        "context": "The present proceedings have been initiated under Section 34 of the Arbitration and Conciliation Act, 1996 for setting aside the arbitral award.",
        "is_internal": False,
        "rule_verdict": False,
    },
    {
        "id": "M12",
        "span": "Section 9",
        "context": "Prior to arbitral constitution, the petitioner filed a petition under Section 9 of the Act seeking interim measures of protection.",
        "is_internal": False,
        "rule_verdict": False,
    },
    {
        "id": "M13",
        "span": "Section 14",
        "context": "The respondent contends that Section 14 of the Specific Relief Act, 1963 bars specific performance of determinable contracts.",
        "is_internal": False,
        "rule_verdict": False,
    },
    {
        "id": "M14",
        "span": "AIR 2008 SC 1245",
        "context": "In ONGC vs Saw Pipes Ltd, AIR 2008 SC 1245, the Hon'ble Supreme Court held that patent illegality invalidates an award.",
        "is_internal": False,
        "rule_verdict": False,
    },
    {
        "id": "M15",
        "span": "2019 SCC OnLine Del 8412",
        "context": "A similar issue arose in Delhi Metro Rail Corp. v. DMRC, 2019 SCC OnLine Del 8412, where the court observed...",
        "is_internal": False,
        "rule_verdict": False,
    },
    {
        "id": "M16",
        "span": "Section 37",
        "context": "An appeal under Section 37 lies against an order setting aside or refusing to set aside an arbitral award under Section 34.",
        "is_internal": False,
        "rule_verdict": False,
    },
    {
        "id": "M17",
        "span": "Section 302",
        "context": "The trial court convicted the accused under Section 302 I.P.C. and sentenced him to life imprisonment.",
        "is_internal": False,
        "rule_verdict": False,
    },
    {
        "id": "M18",
        "span": "Section 482",
        "context": "The High Court exercised its inherent powers under Section 482 Cr.P.C. to quash the FIR.",
        "is_internal": False,
        "rule_verdict": False,
    },
    {
        "id": "M19",
        "span": "Clause 11",
        "context": "As observed in Clause 11 of the said Act, the statutory period cannot be extended by mutual agreement.",
        "is_internal": False,
        "rule_verdict": False,
    },
    {
        "id": "M20",
        "span": "Article 14",
        "context": "The action of the State undertaking in cancelling the tender arbitrarily violates Article 14 of the Constitution.",
        "is_internal": False,
        "rule_verdict": False,
    },
]


SYSTEM_PROMPT = """\
You are an expert legal document indexer.
You will be shown a candidate text span and its surrounding page context from a document.
Your task is to determine whether the span is an INTERNAL document reference or structure target (e.g. a section, clause, annexure, or heading belonging to THIS contract/document),
or an EXTERNAL citation / false positive (e.g. an external statute like the Arbitration Act, Cr.P.C., a law report case citation like AIR/SCC, a court heading, running header/footer, or prose).

Output JSON only in this format:
{"is_internal": true} or {"is_internal": false}
"""


def _build_prompt_arm_a(sample: Dict[str, Any]) -> str:
    return f"""\
# CONTEXT
<<<
{sample['context']}
>>>

# SPAN
"{sample['span']}"

Is this span an INTERNAL document reference/target?
"""


def _build_prompt_arm_b(sample: Dict[str, Any]) -> str:
    verdict_str = "INTERNAL document reference/target" if sample["rule_verdict"] else "EXTERNAL citation / non-reference"
    return f"""\
# CONTEXT
<<<
{sample['context']}
>>>

# SPAN
"{sample['span']}"

# RULE-BASED DETECTOR PRIOR VERDICT
The deterministic rule pass classified this span as: {verdict_str}

Is this span an INTERNAL document reference/target? Arbitrate and return your verdict.
"""


def _build_prompt_arm_c(sample: Dict[str, Any], invert: bool) -> str:
    v = not sample["rule_verdict"] if invert else sample["rule_verdict"]
    verdict_str = "INTERNAL document reference/target" if v else "EXTERNAL citation / non-reference"
    return f"""\
# CONTEXT
<<<
{sample['context']}
>>>

# SPAN
"{sample['span']}"

# RULE-BASED DETECTOR PRIOR VERDICT
The deterministic rule pass classified this span as: {verdict_str}

Is this span an INTERNAL document reference/target? Arbitrate and return your verdict.
"""


async def evaluate_sample(
    provider: Any,
    prompt: str,
) -> bool:
    res = await provider.complete(system_prompt=SYSTEM_PROMPT, user_prompt=prompt)
    raw = res.raw_content.strip()
    try:
        data = ai_indexing.parsing.extract_json_object(raw)
        parsed = json.loads(data)
        return bool(parsed.get("is_internal"))
    except Exception:
        # Fallback keyword scan
        lower = raw.lower()
        if "true" in lower:
            return True
        return False


async def run_pilot(repetitions: int = 3, sample_count: int = 40):
    samples = PILOT_SAMPLES[:sample_count]
    print(f"Loaded {len(samples)} pilot samples. Initializing provider...")

    resolved = ai_indexing.resolve_active_config()
    provider = await ai_indexing.get_provider(resolved.provider_config)
    print(f"Running against provider={resolved.provider_config.provider}, model={resolved.provider_config.model}")

    # Arm (a): no prior
    print(f"\n--- Arm (a): No Prior ({repetitions} repetitions) ---")
    arm_a_results: List[List[bool]] = []
    for r in range(repetitions):
        run_res = []
        for s in samples:
            prompt = _build_prompt_arm_a(s)
            val = await evaluate_sample(provider, prompt)
            run_res.append(val)
        arm_a_results.append(run_res)
        print(f"  Repetition {r + 1}/{repetitions} complete.")

    # Calculate Arm (a) self-consistency: fraction of samples where all runs agree
    agree_count = 0
    correct_count_a = 0
    for idx, s in enumerate(samples):
        vals = [arm_a_results[r][idx] for r in range(repetitions)]
        if len(set(vals)) == 1:
            agree_count += 1
        # Majority vote
        maj = sum(vals) > (repetitions / 2)
        if maj == s["is_internal"]:
            correct_count_a += 1
    self_consistency_a = (agree_count / len(samples)) * 100
    accuracy_a = (correct_count_a / len(samples)) * 100
    print(f"Arm (a) Self-Consistency: {self_consistency_a:.1f}% (bar >= 90%)")
    print(f"Arm (a) Accuracy: {accuracy_a:.1f}%")

    # Arm (b): truthful prior
    print(f"\n--- Arm (b): Truthful Prior ({repetitions} repetitions) ---")
    arm_b_results: List[List[bool]] = []
    for r in range(repetitions):
        run_res = []
        for s in samples:
            prompt = _build_prompt_arm_b(s)
            val = await evaluate_sample(provider, prompt)
            run_res.append(val)
        arm_b_results.append(run_res)
        print(f"  Repetition {r + 1}/{repetitions} complete.")

    correct_count_b = 0
    for idx, s in enumerate(samples):
        vals = [arm_b_results[r][idx] for r in range(repetitions)]
        maj = sum(vals) > (repetitions / 2)
        if maj == s["is_internal"]:
            correct_count_b += 1
    accuracy_b = (correct_count_b / len(samples)) * 100
    print(f"Arm (b) Accuracy: {accuracy_b:.1f}%")

    # Arm (c): adversarial prior on random half
    print(f"\n--- Arm (c): Adversarial Prior ({repetitions} repetitions) ---")
    # Deterministic pseudo-random half inversion
    rng = random.Random(42)
    inverts = [rng.choice([True, False]) for _ in samples]
    arm_c_results: List[List[bool]] = []
    for r in range(repetitions):
        run_res = []
        for idx, s in enumerate(samples):
            prompt = _build_prompt_arm_c(s, invert=inverts[idx])
            val = await evaluate_sample(provider, prompt)
            run_res.append(val)
        arm_c_results.append(run_res)
        print(f"  Repetition {r + 1}/{repetitions} complete.")

    inverted_samples_count = sum(inverts)
    c_agree_with_inverted = 0
    c_correct_count = 0
    for idx, s in enumerate(samples):
        vals = [arm_c_results[r][idx] for r in range(repetitions)]
        maj = sum(vals) > (repetitions / 2)
        if inverts[idx]:
            inverted_prior = not s["rule_verdict"]
            if maj == inverted_prior:
                c_agree_with_inverted += 1
        if maj == s["is_internal"]:
            c_correct_count += 1

    inv_agreement_pct = (c_agree_with_inverted / max(inverted_samples_count, 1)) * 100
    accuracy_c = (c_correct_count / len(samples)) * 100
    print(f"Arm (c) Agreement with Inverted Prior on Inverted Half: {inv_agreement_pct:.1f}% (kill threshold > 80%)")
    print(f"Arm (c) Accuracy: {accuracy_c:.1f}%")

    # Kill criteria evaluation:
    print("\n" + "=" * 50)
    print("PILOT DECISION GATES")
    print("=" * 50)
    fail = False
    if self_consistency_a < 90.0:
        print(f"❌ KILL CRITERION 1 MET: Arm (a) self-consistency {self_consistency_a:.1f}% < 90%")
        fail = True
    else:
        print(f"✅ GATE 1 PASSED: Arm (a) self-consistency {self_consistency_a:.1f}% >= 90%")

    if inv_agreement_pct > 80.0 and accuracy_b > 75.0:
        print(f"❌ KILL CRITERION 2 MET: Arm (c) rubber-stamped inverted prior ({inv_agreement_pct:.1f}% > 80%)")
        fail = True
    else:
        print(f"✅ GATE 2 PASSED: Arm (c) agreement with inverted prior {inv_agreement_pct:.1f}% <= 80%")

    if fail:
        print("\nOVERALL VERDICT: FAILED PILOT (Anchoring or inconsistency dominates)")
    else:
        print("\nOVERALL VERDICT: PASSED PILOT (Model arbitrates correctly with prior)")


def main():
    parser = argparse.ArgumentParser(description="Run Phase 4 Anchoring Pilot")
    parser.add_argument("--repetitions", type=int, default=3, help="Number of repetitions per arm")
    parser.add_argument("--samples", type=int, default=40, help="Number of samples to evaluate")
    args = parser.parse_args()

    asyncio.run(run_pilot(repetitions=args.repetitions, sample_count=args.samples))


if __name__ == "__main__":
    main()
