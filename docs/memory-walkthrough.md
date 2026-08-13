# Memory: a walkthrough you can follow in the browser

One conversation, thirteen messages, starting from nothing. Each step adds one
brick and shows you the brick that just landed.

Type the message, wait for the reply to finish, then look where the step tells
you to look. Nothing here needs the terminal.

**Where things are**

- **The switch** — the *Conversation Context* popover above the composer has a
  **Memory** toggle. Off, nothing is read and nothing is written.
- **What the reply saw** — the memory chip on each assistant message.
- **What was written** — the memory-write panel on that same message. It
  appears a moment *after* the reply finishes, because the writer runs once the
  turn is over.
- **The store** — the **Memory** page: Profile, the cards below it, Archive
  (replaced memories), Tidy-up (suggestions).

---

## Step 0 — start empty

Memory page → **Clear all**. Profile empty, no cards. Now turn the **Memory**
toggle **on** in a new conversation.

Everything below happens in that one conversation.

---

## Step 1 — say who you are

> hey — I'm Abbas, that's what everyone calls me

**Look at:** the Memory page → Profile.

**Expect:** an **Identity** line, "They are called Abbas." You never said
"remember this", and it still went into the profile — what to call someone is
how to address them, and it would be wrong on every turn it failed to reach.

---

## Step 2 — a hard constraint

> quick heads up, I'm severely allergic to peanuts

**Look at:** Profile → **Constraints**, and the cards below.

**Expect:** it is in *both*. The card is the memory; the profile line is the
same memory shown in the file that goes into every prompt. Safety never waits
to be looked up.

---

## Step 3 — where you live

> I live in Lahore

**Expect:** an **Identity** line. Remember this one — step 12 moves you.

---

## Step 4 — what you do

> I'm a backend engineer, mostly Django and Postgres these days

**Look at:** the cards.

**Expect:** two cards, not one — the job and the stack are separate facts, so a
later change of job cannot quietly delete the stack. Neither reaches the
profile: they are looked up when a question needs them.

---

## Step 5 — how you want to be answered

> keep your answers short by the way, I really don't like walls of text

**Expect:** a **Communication** line in the profile. Like step 1, no permission
needed — it is a request about the next answer, not a disclosure about you.

---

## Step 6 — a rule, not a fact

> when you review my code, be blunt about it. no compliments, just what's wrong

**Look at:** the cards — this one is a *behavior*.

**Expect:** it is stored with its situation ("when reviewing code"), and it is
**not** in the profile. A rule should only appear when its moment arrives.

---

## Step 7 — the moment arrives (the important one)

> here's a function I wrote, take a look:
>
> ```
> def parse(x):
>     return x.split(',')[1]
> ```

**Look at:** the memory chip on the reply.

**Expect:** the step-6 rule is there — and notice your message never said
"review", "blunt", or "rule". That is the whole point of the behavior layer: it
is fetched by the situation, not by matching words. The reply should be blunt.

---

## Step 8 — the other important one

> book me somewhere nice for dinner tonight

**Expect:** the reply already knows about the peanut allergy, without you
mentioning food restrictions. This is why safety facts go into the profile
instead of waiting to be searched — the turn where an allergy matters is
exactly the turn that doesn't mention it.

---

## Step 9 — something private, said in passing

> I've been getting migraines pretty often lately

**Look at:** the write panel, and then the cards.

**Expect:** it is written down and marked **held**. You can see it; answers
cannot. Saying nothing about a migraine costs nothing, so it is held back. (An
allergy is the opposite case, which is why step 2 behaved differently.)

---

## Step 10 — a number

> my portfolio is sitting at about 8M right now

**Expect:** the card reads *"…was approximately 8M on 2026-08-13"* — past
tense, with the date in the sentence. A measured value is only true on the day
it was measured, and read back next year "is 8M" would be a lie no one could
detect.

---

## Step 11 — an unrelated question

> explain how the TCP three-way handshake works

**Look at:** the memory chip.

**Expect:** the profile, and **nothing else**. No allergy, no job, no bouldering
habit. Returning nothing is a correct answer.

---

## Step 12 — change your mind

> actually I moved to Islamabad last week

**Look at:** Profile, then **Archive**.

**Expect:** the profile now says Islamabad. Lahore is not gone — it is in the
Archive, marked as replaced, with Islamabad named as what replaced it. Nothing
is ever deleted; it is retired.

---

## Step 13 — repeat yourself

> reminder, I'm severely allergic to peanuts

**Look at:** the write panel.

**Expect:** **nothing new was stored**, and it says so: the existing memory now
stands on 2 tellings. Repetition is the only durability signal there is, and
Tidy-up uses it to decide what deserves a permanent seat.

---

## After the thirteen: two more things

**Ask about the past.**

> what did I tell you about my code review preference?

The assistant should call the **search_sessions** tool — you will see the tool
call — and read your own words back. That is the transcript layer: exact words,
not a summary. It also takes a date range, so "what did we talk about last
week" searches the week rather than the phrase.

**Open Tidy-up.**

On a store this small it will probably be empty, and empty is the correct
answer — it only speaks when something is genuinely wrong (two cards saying the
same thing, a fact repeated enough to deserve the profile, a slot whose name no
longer matches, a profile over budget). It only ever *suggests*: nothing moves
until you approve it.

---

## The four layers, in one line each

| Layer | What it holds | When it is read |
|---|---|---|
| **Profile** | who you are, how to answer you, hard limits | every single turn |
| **Knowledge** | durable facts about your life and work | when a question reaches for one |
| **Behavior** | rules with a situation attached | when that situation arrives |
| **Transcript** | every message, word for word | when the assistant searches it |

The first three are put into the prompt for you. The fourth is a tool the
assistant chooses to use — because "what did we decide" is obviously a lookup,
while an allergy behind "book me somewhere nice" is something it could never
know to go looking for.

---

## If something looks wrong

Every decision is logged, including the refusals. The write panel on a message
shows what the writer proposed, what was actually done, and which rule changed
it — so "why did it not remember that?" always has an answer on screen.
