---
type: Skill
title: Run an Attested Computation on PostgreSQL
description: "Executor for computations whose runtime is postgres: binds parameters, runs the statement, returns a receipt."
owner: team:platform
tags: [skill, executor, postgres]
status: stable
generated: { by: human:ayomidelasaki@pipeops.io, at: 2026-08-19T15:20:00Z }
verified: { by: human:ayomidelasaki@pipeops.io, at: 2026-08-20T10:30:00Z }
---

# When to use

An [Attested Computation](/computations/deploy-success-rate.md) whose `runtime`
is `postgres` points its `executor.resource` here.

# Preconditions

- The caller holds a read-only role on the analytics replica. Attested
  computations never run against the primary.
- Every parameter declared `required: true` has a caller-supplied value.

# Steps

1. **Load the computation.** Read the fenced block under `# Computation`, or
   the file named by `computation:`. The result is SQL carrying `@name` bind
   variables for each declared parameter.
2. **Bind parameters.** Pass them as server-side parameters. Never
   string-interpolate: the attester rejects a receipt whose `executed_sql`
   shows literal substitution, because a literal is indistinguishable from a
   rewrite.
3. **Run the statement** with a statement timeout of 60 seconds and
   `SET TRANSACTION READ ONLY`.
4. **Assemble the receipt** with exactly the fields in `executor.receipt`:

   ```json
   {
     "statement_id": "pg://<host>/<database>/<pid>-<query_start>",
     "executed_sql": "<the statement as the server parsed it, @name preserved>",
     "result": ["<first row's cells, in select order>"]
   }
   ```

5. **Never modify the computation.** If a required parameter is missing or
   mistyped, refuse and return an error receipt. Do not repair the SQL.

# Post-conditions

Hand the receipt to the concept's `attester.resource`. Do not display the value
until the attester returns `ok: true`.
