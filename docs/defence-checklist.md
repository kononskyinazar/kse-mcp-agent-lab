# Defence / demo checklist

10-15 minutes, delivered independently. Every numbered item below is a required
demonstration step from the assignment; the ordering follows the suggested timing table.

## Before the defence

- [ ] `.env` filled from `.env.example`; no secret is in git (`git log -p | grep -i` spot check)
- [ ] Custom MCP server starts from a clean clone using only README instructions
- [ ] Existing MCP server pinned to the instructor-announced version, and running
- [ ] Fixtures recorded and replay mode verified (`CUSTOM_MCP_OFFLINE=true`)
- [ ] Prepared valid input, a second *different* valid input, and one invalid input
- [ ] One value chosen to trace end-to-end, from its source to the final output
- [ ] Screen readable: font size increased, tool-call logging visible

## Segment 1 - Startup and architecture (2 min)

1. [ ] Start the custom MCP server **independently**, in its own terminal, before the agent
2. [ ] Start the agent; show it discovers **both** MCP connections and lists their tools
3. [ ] One-sentence architecture statement: agent process, MCP client, two servers, data source

## Segment 2 - Existing MCP server in an agent flow (2-3 min)

4. [ ] Invoke a tool from the approved existing server successfully
5. [ ] Run a flow where that result **changes a later step**, and say out loud which step
6. [ ] Explain that tool's contract: name, model-facing description, arguments and
       constraints, returned result, errors, side effects
7. [ ] State why this server has a role here at all

## Segment 3 - Custom MCP end-to-end (3-4 min)

8. [ ] Run one complete workflow driven by the custom server
9. [ ] Show evidence that at least three custom tools are exposed (discovery output)
10. [ ] Explain one important tool contract and one design decision behind it
11. [ ] Point at a side effect explicitly, or state that there is none

## Segment 4 - Failure and replay (2 min)

12. [ ] Trigger one realistic failure of the **existing** server or its tool
       (server stopped / invalid key / missing resource / invalid tool input)
13. [ ] Show how the agent surfaces and handles it - not a silent swallow, not a crash
14. [ ] Show an invalid input to a custom tool and the distinguishable error it returns
15. [ ] Show replay/offline mode producing the same result through the normal parse path

## Segment 5 - Questions and variation (3-4 min)

16. [ ] Re-run with a **different valid input** and show the output differ accordingly
17. [ ] Trace one value from its source through each hop to the final output
18. [ ] Be ready to make a small configuration-level change live

## Recorded-video variant

Same sequence, one continuous take, screen + camera + voice. Trimming head and tail only.
Items 16 and 17 are mandatory rather than on-request in this format.
