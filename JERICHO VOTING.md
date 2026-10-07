JERICHO VOTING - WEIGHTED GOVERNANCE DRAFT 2026-10-06

1. Who votes:
Active Watch Command ranks, specialists, leaders and registered Equerrys. Bots,
Reserves, Interred Brother and Bladeguard are excluded. Eligibility is frozen
when the poll opens. Existing polls retain their original unweighted rules.

2. Destination and turnout:
The command requires the destination Discord role and subject member. Each specialist formation
is a separate voting group; battle-line comprises Sergeant through Watch Master
fortress-wide. Dreadnoughts use their own voting group.
60% of the eligible destination group must vote. Turnout is displayed as a
percentage and counts people, never vote weights. Outside ballots cannot meet
destination turnout. An empty required group blocks creation.

3. Ballot weights (configurable starting values):
Sergeant / specialist: 1.00
Veteran Sergeant: 1.15
Lieutenant / Equerry: 1.30
Captain / cadre leader: 1.50
Watch Master: 1.75
Use the highest applicable tier, not the sum of retained rank roles.
First Blade uses the specialist tier; Honored Dreadnought uses the Lieutenant
tier; Venerable Dreadnought uses the leader tier.
High Command (Captains, leaders, Equerrys and Watch Master) adds 0.25.
Then multiply by 2 if the voter belongs to the destination group.
The multiplier gives greater influence, not guaranteed destination control.

4. Passing and revotes:
80% of the total yes+nay ballot weight must be yes. No abstain option.
Missing required turnout or a result within five percentage points of 80%
requires a revote, including results on or just above the passing threshold.

5. Recusal:
Every poll requires a subject member. They cannot vote and are removed from
all applicable turnout denominators.

6. High Command appointments:
Captain, cadre leader, Equerry and Watch Master appointments require both the
destination group and current High Command independently to meet 60% turnout.
A person belonging to both contributes to both turnout percentages but casts
only one weighted ballot. The passing threshold remains 80% weighted yes.

7. Equerry confirmation:
For an Equerry appointment, select the destination's ordinary specialist role
and set equerry_appointment:true. No Equerry Discord role is required.
Current officeholders are explicitly registered by member ID and destination
in governance_poll.equerry_assignments. Passing a poll does not automatically
grant roles or register an Equerry; staff enact and record the appointment.

Vote identities and per-option totals stay hidden while the poll is open.
The final result shows aggregate ballot counts and weighted shares, not names
or separate destination/High Command approval splits.