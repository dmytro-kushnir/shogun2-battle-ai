# Related work (reading list)

Candidates to read before writing section 2. **Every entry must be checked against the actual paper (authors,
year, venue, DOI) before it is cited**; the notes below are from memory and say why each might matter, nothing
more. Add a BibTeX entry and a one-line finding once read.

## Game AI for real-time strategy (the classical baseline)

- [ ] Ontañón et al., survey of real-time strategy game AI research and competition in StarCraft (IEEE TCIAIG,
      2013). Why: framing of RTS AI levels (strategy, tactics, reactive control), matches our two-rate split.
- [ ] Vinyals et al., AlphaStar, grandmaster level in StarCraft II with multi-agent RL (Nature, 2019). Why: the
      trained-agent end of the spectrum; we use no training.

## LLM agents in games

- [ ] Ma et al., large language models play StarCraft II: benchmarks and a chain-of-summarization approach
      (arXiv 2023; check the NeurIPS version). Why: closest LLM-in-RTS work; text observation, macro level.
- [ ] Qi et al., CivRealm (ICLR 2024). Why: LLM agents in a strategy game environment.
- [ ] Wang et al., Voyager, an open-ended embodied agent with LLMs (Minecraft, 2023). Why: LLM writes control code
      over a game API; skill library.
- [ ] Paglieri et al., BALROG benchmark of LLM agents on games (2024). Why: evaluation methodology for LLM game
      agents.
- [ ] A survey of LLM-based game agents (e.g. Hu et al., 2024). Why: positioning.
- [ ] rome-battle-ai (Cegli865, GitHub): LLM general for *Total War: Rome Remastered* through a process hook. Why:
      direct predecessor and inspiration; different access route (memory hook vs. the game's script API).

## Hierarchical / two-rate control with LLMs

- [ ] Ahn et al., SayCan (2022), and related "LLM plans, skills execute" robotics work. Why: high-level LLM over
      low-level controllers is the same split as plan + executor.
- [ ] Yao et al., ReAct (ICLR 2023). Why: reasoning-and-acting loop; our plan loop is a structured variant.
- [ ] Kahneman, *Thinking, Fast and Slow* (2011). Why: only as the metaphor in the title, if kept.

## Game modding / scripting interfaces as research platforms

- [ ] Work using commercial games' scripting or modding APIs for AI research (to search: e.g. Minecraft
      (Malmo), StarCraft (BWAPI, PySC2), others). Why: argues why a shipped script API is a valid research
      interface.

## Prior work by the author

- Kushnir, D. A reproducible benchmark for trophallaxis state detection in *Camponotus fellah* using YOLO26 and
  RF-DETR. *Discover Artificial Intelligence* 6, 1042 (2026). doi:10.1007/s44163-026-01953-2. Why: same
  reproducibility practice (fixed protocol, released artifacts, explicit scope).
