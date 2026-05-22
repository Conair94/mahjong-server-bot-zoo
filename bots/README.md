# Bots

## `sample-botzone/` (git submodule)

Upstream pin of [ailab-pku/Chinese-Standard-Mahjong](https://github.com/ailab-pku/Chinese-Standard-Mahjong) at `master`. Provides the official Botzone CSM reference bot (`sample-bot-Botzone/sample.cpp`) and judge (`judge/main.cpp`).

After cloning this repo, fetch the submodule with:

```sh
git submodule update --init bots/sample-botzone
```

### Status: vendored, not yet built

The reference bot and judge are **C++** and depend on `jsoncpp` plus the C++ build of `MahjongGB` (`fan-calculator-usage/Mahjong-GB-CPP/`). Compiling them is **Step 5.3b** — the deferred half of the S1 exit, per [CHECKLIST.md](../CHECKLIST.md). Once the binaries are produced, they slot into `BotRunnerAdapter` like any other Botzone-protocol bot.

For Step 5.3a (in-tree integration), see `python-reference/` below.

## `python-reference/`

In-tree Python rule-based bot used in the four-bot integration test. Uses [`mahjong.bots.sdk`](../mahjong/bots/sdk/__init__.py) plus the Botzone CSM request parser. Plays default actions; sufficient to exercise the full grammar without requiring upstream compilation.
