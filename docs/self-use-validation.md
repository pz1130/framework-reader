# The one time you really use it (main spec §7.3.7)

The only validation question this tool has is **"what would you do without
it?"** - if eight times out of ten the answer is "I would just ask the model
directly", the core selling point does not hold.

**But you cannot answer that yet.** The `framework-reader` Skill is installed
globally: in Claude Code, any framework question automatically triggers
`fr search`. You never experience "with the tool" and "without the tool"
separately; mixed together everything feels "pretty useful" and you cannot tell
whether the tool or the model is doing the work.

So when the real moment comes, **deliberately separate the two sides once**:

**① Ask elsewhere first.** Phone app, web chat, another vendor's model - **not
in this repo's Claude Code session** (the Skill auto-triggers and contaminates
the control on the spot). Ask the question you would ask anyway:

> I'm writing a company policy on "server log retention and auditing". Under
> NIST CSF 2.0, which controls does it map to, and what evidence is usually
> prepared for each?

**② Save the answer verbatim** (screenshot or paste into a file). That is the
control; it may not be edited afterwards.

**③ Then query the tool.**

```bash
fr search log retention
fr show NIST-CSF-2.0:DE.CM-01
```

**④ Write one note answering a single question: what did the tool give you that
the answer above did not?**

```bash
fr usage --note "Writing a log-retention policy. The model gave DE.CM-01/PR.PS-04
and a general direction; the tool additionally gave the auditor_asks probe 'what
is the basis for the retention period' and the official mapping to ISO A.8.15
with sources. Without the tool I would have used the model's answer."
```

**That note is the entire deliverable.** If you cannot write what the tool
added, the answer is that it added nothing - also a conclusion, and the kind
that saves months.

No thresholds, no trial counts. The peer blind test has a pass line fixed in
advance because the 3-5 judges are a one-shot resource; self-usage is free and
repeatable, so giving it the same gate would be over-engineering.
