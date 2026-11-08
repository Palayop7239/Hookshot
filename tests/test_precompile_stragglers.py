"""A graphics pipeline the startup precompile finishes after the game went on is let go, not put in the pipeline map.

Since 1f922f7 the precompile stops waiting once nothing finished for RX_VK_PRECOMPILE_STALL_S seconds: the game starts and
the last builds go on behind it. A compute one finished then was already destroyed (pre->abandoned, under pre->mu). A
graphics one still went into `pipelines` under note_mu, while the game's graphics_pipeline looks the map up and adds to it
under ParLock (no lock at all with one replay thread), never note_mu: two threads changing an unordered_map, a rehash under a
lookup. It is now let go as the compute one is (the driver's pipeline cache keeps it for the game's own build), and pre->mu
is held over the insert, so the game cannot go on halfway through one.

Reads gpu/vk/vk_draw.cpp.
python -I -B tests/test_precompile_stragglers.py
"""
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]


def block(text, marker, start=0):
    begin = text.index(marker, start)
    end = text.index("{", begin) + 1
    depth = 1
    while depth:
        depth += (text[end] == "{") - (text[end] == "}")
        end += 1
    return text[begin:end]


def need(cond, what):
    if not cond:
        raise SystemExit("FAIL: " + what)


def at(text, s, start=0):
    """Where s is in text from start, or a failure naming it"""
    i = text.find(s, start)
    need(i >= 0, "not found where expected: " + s)
    return i


def main():
    draw = (ROOT / "gpu" / "vk" / "vk_draw.cpp").read_text(encoding="utf-8")
    run = block(draw, "void precompile_run(")
    worker = block(run, "const auto worker = [pre, total] {")

    # (the game's side: what the map is guarded by while the game runs, the reason the precompile may not touch it then)
    lookup = block(draw, "PipeEntry* graphics_pipeline(")
    need(lookup.index("ParLock lk;") < lookup.index("pipelines.find(key)"), "graphics_pipeline looks the map up under ParLock")
    need("note_mu" not in lookup[:lookup.index("pipelines.emplace(key")],
         "this test assumes the game's lookup takes no note_mu; if it does now, the race it guards against is gone")

    # (the compute side, as 1f922f7 made it)
    need("if (!pre->abandoned) p.compute_pipeline = made;" in worker, "a compute pipeline is kept only before the game went on")

    # (the graphics side)
    graphics = worker[at(worker, "PipeNote& n = *notes[i - computes.size()];"):]
    built = at(graphics, "const VkPipeline made = build_graphics(")
    # (a graphics pipeline finished after the game went on must be let go; the map is filled only before, under pre->mu)
    held = at(graphics, "std::lock_guard lk_pre(pre->mu);", built)
    check = at(graphics, "if (pre->abandoned) {", held)
    let_go = at(graphics, "vkDestroyPipeline(dev.device, made, nullptr);", check)
    other = at(graphics, "} else {", let_go)
    insert = at(graphics, "pipelines.emplace(n.key", other)
    need(at(graphics, "std::lock_guard lk(note_mu);", other) < insert, "the insert still holds note_mu")
    need(graphics.count("pipelines.emplace(") == 1, "no other way into the map from the precompile")

    # (the main thread says the game goes on under the same lock)
    loop = run[at(run, "while (done.load() < total) {"):]
    need(at(loop, "std::lock_guard lk(pre->mu);") < at(loop, "pre->abandoned = true;"), "abandoned is set under pre->mu")
    print("precompile stragglers ok")


if __name__ == "__main__":
    main()
