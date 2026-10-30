-- Exercise the real round summary, adaptation and second-wind code with engine-result fixtures.
-- Run with Lua 5.1 from the repository root; the same file can run as an isolated engine module.
local f = assert(io.open('external/mods/woo_brain.lua', 'r'))
local source = f:read('*a')
f:close()
local selected, outcomes, messages, maps = 1, {}, {}, {}
local sink = {write = function(_, line) messages[#messages + 1] = line end, close = function() end}
local env = setmetatable({
	getCommandLineValue = function() return nil end,
	io = {open = function(_, mode) if mode == 'a' then return sink end end},
	hook = {add = function() end}, addHotkey = function() end,
	player = function(pn) selected = pn return outcomes[pn] ~= nil end,
	win = function() return outcomes[selected] == 'win' end,
	lose = function() return outcomes[selected] == 'loss' end,
	drawGame = function() return outcomes[selected] == 'draw' end,
	mapSet = function(key, value) maps[key] = value end, setAILevel = function() end,
}, {__index = _G})
local chunk = assert(loadstring(source .. '\nreturn W, summary, applyRound, step'))
setfenv(chunk, env)
local W, summary, applyRound, step = chunk()

local function fighter(pn)
	return {def = 'fixture' .. pn .. '.def', mode = 'overmind', ban = {}, stats = {},
		tries = 0, guards = 0, iq = 0.5, roundsLost = 0, opp = {}, adapt = {},
		mem = {enemy = {dmg = {melee = 100}, rounds = 0}}, T = {atk = 1, def = 1, ai = 6}}
end

local function run(result, lives)
	outcomes, messages, maps = result, {}, {}
	W.F, W.round, W.verbose = {}, 1, false
	local snapshots = {}
	for pn, value in ipairs(lives) do
		W.F[pn] = fighter(pn)
		snapshots[pn] = {pn = pn, side = pn % 2 == 1 and 1 or 2, life = value, lifeMax = 1000}
	end
	summary(snapshots)
	return table.concat(messages)
end

-- Both fighters can survive a time-over; zero life is not the loss criterion.
local text = run({'win', 'loss'}, {900, 500})
assert(W.F[2].roundsLost == 1, 'time-over loser must receive a loss')
assert(W.F[1].roundsLost == 0, 'time-over winner must not receive a loss')
assert(math.abs(W.F[2].adapt.enemy.melee - 0.60) < 1e-9, 'loser must learn at the loss rate')
assert(math.abs(W.F[1].adapt.enemy.melee - 0.27) < 1e-9, 'winner must learn at the win rate')
assert(text:find('result=loss', 1, true), 'round log must expose the authoritative outcome')
applyRound(W.F[2], {pn = 2}, true, 2)
assert(W.F[2].secondWind > 0, 'eligible time-over loser must receive second wind next round')
assert(maps._woo_atk > 1 and maps._woo_def > 1, 'second wind must reach the fighter maps')

run({'loss', 'win'}, {400, 800})
assert(W.F[1].roundsLost == 1 and W.F[2].roundsLost == 0, 'either side can lose on time')
run({'loss', 'win'}, {0, 800})
assert(W.F[1].roundsLost == 1 and W.F[2].roundsLost == 0, 'KO outcomes must remain correct')
text = run({'draw', 'draw'}, {0, 0})
assert(W.F[1].roundsLost == 0 and W.F[2].roundsLost == 0, 'double KO is a draw, not two losses')
assert(text:find('result=draw', 1, true) and not text:find('won round', 1, true), 'draws must be labelled as draws')
run({'draw', 'draw'}, {500, 500})
assert(W.F[1].roundsLost == 0 and W.F[2].roundsLost == 0, 'time-over draw must not increment losses')
run({'win', 'loss', 'win', 'loss'}, {0, 500, 800, 0})
assert(W.F[1].roundsLost == 0 and W.F[3].roundsLost == 0, 'KO member of winning team still wins')
assert(W.F[2].roundsLost == 1 and W.F[4].roundsLost == 1, 'all losing-team members receive the loss')

-- The first end-of-round frame may still change to a draw; summarize only once the result settles.
local count, state, tick, round = 0, 3, 1, 1
for i = 1, 100 do
	local name = debug.getupvalue(step, i)
	if not name then break end
	if name == 'summary' then debug.setupvalue(step, i, function() count = count + 1 end) end
	if name == 'snap' then debug.setupvalue(step, i, function() return nil end) end
	if name == 'overlay' or name == 'drawCaptions' then debug.setupvalue(step, i, function() end) end
end
W.F, W.round, W.matchNo, W.logged = {}, 1, 1, false
env.player = function() return true end
env.gameTime = function() tick = tick + 1 return tick end
env.roundState = function() return state end
env.roundNo = function() return round end
env.matchNo = function() return 1 end
step()
assert(count == 0, 'transient roundState 3 must not update learning')
state = 4
step(); step()
assert(count == 1, 'settled round must be summarized exactly once')
round, state = 2, 3
step()
assert(count == 1, 'next round must also wait for the settled result')
state = 4
step()
assert(count == 2, 'each completed round must be summarized once')
print('brain outcome/adaptation/second-wind regressions passed')
-- A direct engine module run must finish without entering the menu loop.
if getCommandLineValue then os.exit(0) end
