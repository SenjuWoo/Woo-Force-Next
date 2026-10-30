--[[ Woo Force brain - universal AI pilot + team tactics for Ikemen GO 1.0

What it does, per fighter, every game tick:
  pilot  (own AI is basic/none): the brain plays the character through a virtual
         controller (map _woo_keys -> data/woo_brain.zss -> AssertInput). It performs
         the character's real command inputs from its parsed move catalog, so the
         character's own State -1 rules decide what comes out. It guards, punishes,
         chains combos, spaces by persona, and coordinates with teammates.
  coach  (own AI is strong): the character's AI keeps playing; the brain only sets
         its AI level by power tier and applies power scaling.
Data:    woo/roster/chars/<slug>.json from woo/tools/roster_analyzer.py
Log:     woo/logs/brain.log (per-round summary, errors)
Keys:    F7 brain overlay on/off, F8 brain on/off (live A/B against stock AI)
Flags:   -woo off            disable the brain for this run
         -woo.p<N> own|pilot  force a mode for player N (A/B tests)
]]

local VERSION = '0.9.0-beta.2' -- keep in step with CHANGELOG.md (tests/test_repo.py enforces it)

local W = {
	enabled = getCommandLineValue('-woo') ~= 'off',
	overlay = 'auto', -- auto: only during the intro and the first 4 s of a round (F7 cycles auto/on/off)
	F = {},          -- per player number: fighter brain state
	aiSlot = {},     -- slots AI-controlled this match (a Turns member inherits it; initFighter)
	remap0 = {},     -- per piloted player: its real input mapping (restored while the pause menu is open)
	hist = {},       -- per player: last 64 ticks of state, for reaction delay
	lastTick = -1,
	round = -1,
	err = nil,
	verbose = getCommandLineValue('-woo.verbose') ~= nil,
	probe = getCommandLineValue('-woo.probe') ~= nil,
	trace = tonumber(getCommandLineValue('-woo.trace') or ''), -- log every Nth tick per fighter
	guardtest = getCommandLineValue('-woo.guardtest') ~= nil,
	-- test aid: -woo.shot N saves a game frame every N seconds of fighting
	shot1 = tonumber(getCommandLineValue('-woo.shot1') or ''), -- one frame N ticks into the first round (1 = 120)
	shot = tonumber(getCommandLineValue('-woo.shot') or '') and tonumber(getCommandLineValue('-woo.shot')) * 60,
	endafter = tonumber(getCommandLineValue('-woo.endafter') or ''), -- crash scan: end the match N ticks into the fight
}
woo_brain = W -- global so hotkeys can reach it

-- 0 = off, 1 = full. Scales the tier attack/defence multipliers below.
local POWER_SCALING = 1.0
local ROSTER_DIR = 'woo/roster/chars/'
local LOG_PATH = getCommandLineValue('-woo.log') or 'woo/logs/brain.log' -- per-process log for parallel test runs
local DEAD_INPUT_SLOT = 8 -- input slots past Config.Players read nothing: no keyboard bleed
-- Finisher-gate calibration. A super's real entry threshold is not always its total cost (Shishio's skill_4 is
-- two PowerAdd -500 but is entered at 300): when one starts with the gate shut it is cancelled and the fighter's
-- ceiling for that cost is halved from the power it had (never under 100). A super that starts with almost no power,
-- or leaks a second time, does not really need meter (Mr1_Daz_Bones' Atomic Spurt): it is marked free and left alone.
-- Kept for good: <def> TAB <cost> TAB <ceiling>, or <def> TAB free TAB <state>.
local LEAK_PATH = getCommandLineValue('-woo.learned') or 'woo/roster/gate_learned.tsv'
W.leak, W.free = {}, {}
do
	local f = io.open(LEAK_PATH, 'r')
	if f then
		for ln in f:lines() do
			local d, c, v = ln:match('^(.-)\t(%w+)\t(%d+)$')
			if d and c == 'free' then
				W.free[d] = W.free[d] or {}
				W.free[d][tonumber(v)] = true
			elseif d and tonumber(c) then
				W.leak[d] = W.leak[d] or {}
				W.leak[d][tonumber(c)] = math.max(100, tonumber(v))
			end
		end
		f:close()
	end
end

local BIT = {U = 1, D = 2, B = 4, F = 8, a = 16, b = 32, c = 64, x = 128, y = 256, z = 512, s = 1024}

-- Power tiers: how sharp the brain plays and how hard the fighter hits.
-- react = frames before it responds to something new, skill = 0..1 execution/decision quality.
local TIER = {
	[1] = {react = 16, skill = 0.35, ai = 4, atk = 0.80, def = 0.85},
	[2] = {react = 12, skill = 0.50, ai = 5, atk = 0.90, def = 0.92},
	[3] = {react = 9,  skill = 0.65, ai = 6, atk = 1.00, def = 1.00},
	[4] = {react = 7,  skill = 0.78, ai = 7, atk = 1.08, def = 1.06},
	[5] = {react = 5,  skill = 0.88, ai = 8, atk = 1.18, def = 1.14},
	[6] = {react = 3,  skill = 0.96, ai = 8, atk = 1.30, def = 1.25},
}

-- Personas: how the character thinks.
--  agg   approach/attack drive     guard  guard probability when threatened
--  range preferred distance (0 close .. 1 full screen)
--  jump  jump-in tendency          dash  dash/run tendency
--  proj  projectile appetite       super  meter spending appetite
--  combo combo continuation        punish punish focus      taunt  showboating
--  team  how much it helps teammates (relay, rescue, pincer)
local PERSONA = {
	effortless = {risk = 1.3, stare = 75, agg = 0.35, guard = 0.95, range = 0.30, jump = 0.03, dash = 0.00, proj = 0.6, super = 0.5, combo = 0.95, punish = 1.0, taunt = 0.30, team = 0.5},
	tactician  = {risk = 1.2, stare = 55, agg = 0.45, guard = 0.85, range = 0.40, jump = 0.08, dash = 0.30, proj = 0.9, super = 0.6, combo = 0.90, punish = 0.9, taunt = 0.05, team = 0.9},
	zoner      = {risk = 1.0, stare = 40, agg = 0.35, guard = 0.75, range = 0.70, jump = 0.10, dash = 0.10, proj = 1.6, super = 0.7, combo = 0.70, punish = 0.6, taunt = 0.05, team = 0.7},
	rushdown   = {risk = 0.6, stare = 15, agg = 0.90, guard = 0.55, range = 0.05, jump = 0.25, dash = 0.80, proj = 0.4, super = 0.8, combo = 0.95, punish = 0.7, taunt = 0.05, team = 0.8},
	warrior    = {risk = 0.8, stare = 45, agg = 0.75, guard = 0.65, range = 0.20, jump = 0.15, dash = 0.50, proj = 0.7, super = 0.9, combo = 0.85, punish = 0.8, taunt = 0.15, team = 0.6},
	berserker  = {risk = 0.3, stare = 5, agg = 1.00, guard = 0.25, range = 0.00, jump = 0.20, dash = 0.90, proj = 0.3, super = 1.3, combo = 0.80, punish = 0.5, taunt = 0.05, team = 0.3},
	trickster  = {risk = 0.8, stare = 20, agg = 0.65, guard = 0.60, range = 0.35, jump = 0.45, dash = 0.60, proj = 0.9, super = 0.7, combo = 0.75, punish = 0.6, taunt = 0.25, team = 0.6},
	brawler    = {risk = 0.9, stare = 25, agg = 0.65, guard = 0.65, range = 0.20, jump = 0.15, dash = 0.40, proj = 0.8, super = 0.8, combo = 0.85, punish = 0.7, taunt = 0.05, team = 0.7},
}

-- Finisher discipline: a super of 1 / 2 / 3 / 4 bars is allowed once the enemy is under (opening point x this)
-- of its life, so the dearer the move, the nearer to the end of the fight it waits (gateOpenAt below).
local SUPER_LEVEL = {1.0, 0.82, 0.66, 0.55}

-- World-unit body sizes for a 320-scale fighter (hurtbox estimates).
local BODY_HALF = 16
local BODY_H = {S = 70, C = 45, A = 60, L = 18}

local function rnd() return math.random() end
local function clamp(v, a, b) return v < a and a or (v > b and b or v) end
local function bits(...)
	local r = 0
	for _, k in ipairs({...}) do r = r + BIT[k] end
	return r
end

-- ------------------------------------------------------------------ logging
local function log(msg)
	local f = io.open(LOG_PATH, 'a')
	if f then
		f:write(os.date('%Y-%m-%d %H:%M:%S ') .. msg .. '\n')
		f:close()
	end
end

-- ------------------------------------------------------------------ profiles
local function slug(def)
	return (def:gsub('\\', '/'):gsub('%u', string.lower):gsub('[^a-z0-9]', function(c)
		return string.format('_%02x', c:byte())
	end))
end

local function loadProfile(def)
	local path = ROSTER_DIR .. slug(def) .. '.json'
	local f = io.open(path, 'r')
	if not f then return nil end
	f:close()
	local ok, prof = pcall(jsonDecode, path) -- Ikemen's jsonDecode takes a file path
	if ok and type(prof) == 'table' then return prof end
	log('profile unreadable: ' .. path .. ' ' .. tostring(prof))
	return nil
end

-- ------------------------------------------------------------------ knowledge and learning
-- Familiarity: how well fighter A knows fighter B before the first punch (0 stranger .. 1 knows every move).
-- A mirror match, a canon rival or partner, a shared faction, or merely the same universe (a stable roll per
-- ordered pair, so Pain may know Naruto's moves better than Naruto knows Pain's). Strangers start blind and
-- scout: knowledge grows with every attack they witness (faster for the clever) and carries over rounds.
-- Learning: what hurt a fighter is counted per opponent; after a lost round the clever counter it (adapt).
local function listHas(t, v)
	if not t or not v then return false end
	for _, x in ipairs(t) do if x == v then return true end end
	return false
end

local COLLECTIVE = {original = true, ['other anime'] = true, ['other games'] = true} -- series buckets, not one universe
local PERSONA_IQ = {tactician = 0.30, effortless = 0.20, trickster = 0.10, zoner = 0.05, berserker = -0.20}

local function pairRoll(a, b) -- stable 0..1 per ordered pair of strings
	local s, h = a .. '>' .. b, 5381
	for i = 1, #s do h = (h * 33 + s:byte(i)) % 4294967291 end
	return (h % 1000) / 1000
end

local function familiarity(Fa, Fb)
	local a, b = Fa and Fa.lore, Fb and Fb.lore
	if a and b then
		if a.key == b.key then return 1.0 end
		if listHas(a.rivals, b.key) or listHas(b.rivals, a.key) then return 0.9 end
		if listHas(a.partners, b.key) or listHas(b.partners, a.key) then return 0.8 end
		for _, g in ipairs(a.groups or {}) do
			if listHas(b.groups, g) then return 0.55 end
		end
	end
	local sa, sb = Fa and Fa.prof and Fa.prof.series, Fb and Fb.prof and Fb.prof.series
	if sa and sa == sb and not COLLECTIVE[sa] then
		return 0.2 + 0.4 * pairRoll(Fa.def or '', Fb.def or '')
	end
	return 0
end

local function ekey(e) -- an opponent's identity for memory (its def: a Turns replacement is a new opponent)
	local Fe = W.F[e.pn]
	return Fe and Fe.def or ('P' .. e.pn)
end

local function knowOf(F, e)
	local k = ekey(e)
	if F.know[k] == nil then F.know[k] = familiarity(F, W.F[e.pn]) end
	return F.know[k]
end

local function rx(F, e) -- reaction delay: the familiar read the opening moves sooner
	return math.max(2, math.floor(F.T.react * (1 - 0.4 * knowOf(F, e)) + 0.5))
end

-- How far an opponent's attacks reach, as far as this fighter knows (the sprite shows some of it at once)
local function reachSeen(F, Fe, e)
	return 50 + ((Fe and Fe.reachW or 50) - 50) * (0.4 + 0.6 * knowOf(F, e))
end

-- What hurt us, so the clever can counter it: super / air (we were airborne) / throw / proj / melee.
local function hitCategory(F, e, d)
	local Fe = W.F[e.pn]
	local m = Fe and Fe.moveBySt[e.st]
	if Fe and Fe.superSt[e.st] then return 'super' end
	if F.stypeLast == 'A' then return 'air' end
	if m and m.throw then return 'throw' end
	if m and (m.proj or m.helper) then return 'proj' end
	if e.mtype ~= 'A' and d > 140 then return 'proj' end -- hit by something it left behind
	return 'melee'
end

-- Every tick: scouting (attacks witnessed raise knowledge) and the hit ledger (damage by cause and opponent).
local function observe(F, s, B)
	for _, e in ipairs(B.enemies) do
		local k = ekey(e)
		local kn = knowOf(F, e)
		local atk = e.mtype == 'A'
		if atk and not F.eAtk[e.pn] and math.abs(e.x - s.x) < 320 then
			F.know[k] = math.min(1, kn + 0.03 + 0.05 * F.iq)
		end
		F.eAtk[e.pn] = atk
	end
	if s.mtype == 'H' and not F.wasHit then
		local near, nd = nil, 1e9
		for _, e in ipairs(B.enemies) do
			local d = math.abs(e.x - s.x)
			if d < nd then near, nd = e, d end
		end
		if near then
			local k = ekey(near)
			local mem = F.mem[k] or {dmg = {}, rounds = 0}
			F.mem[k] = mem
			F.curKey, F.curCat = k, hitCategory(F, near, nd)
			mem.dmg[F.curCat] = mem.dmg[F.curCat] or 0
			F.know[k] = math.min(1, knowOf(F, near) + 0.06 + 0.04 * F.iq)
		end
	end
	F.wasHit = s.mtype == 'H'
	-- (the round-end decay can rebuild a ledger while a hit is still being counted: fin run 2026-10-21 crashed here)
	local mem = F.curKey and F.curCat and F.mem[F.curKey]
	if mem and F.lastLife and s.life < F.lastLife then
		mem.dmg[F.curCat] = (mem.dmg[F.curCat] or 0) + (F.lastLife - s.life)
	end
	F.lastLife = s.life
	if s.mtype ~= 'H' and s.stype ~= 'L' then F.curKey = nil end
	F.stypeLast = s.stype
end

-- End of a round: turn the hit ledger into counters. A lost round teaches fully, a won one a little; the
-- clever learn fast, and what was learnt decays by a third unless it keeps happening.
local function learnRound(F, lost)
	if lost then F.roundsLost = F.roundsLost + 1 end
	local notes = {}
	for k, mem in pairs(F.mem) do
		local tot = 0
		for _, v in pairs(mem.dmg) do tot = tot + v end
		local A = F.adapt[k] or {}
		for cat in pairs({proj = 1, air = 1, throw = 1, super = 1, melee = 1}) do
			local share = tot > 0 and (mem.dmg[cat] or 0) / tot or 0
			local rate = F.iq * (lost and 1.0 or 0.45) * (1 + 0.25 * math.min(mem.rounds, 3))
			A[cat] = clamp((A[cat] or 0) * 0.67 + rate * share * 1.2, 0, 0.9)
		end
		-- an opponent that blocks most of what we throw: mix in throws
		for epn, o in pairs(F.opp) do
			local Fe = W.F[epn]
			if Fe and Fe.def == k and o.hit + o.blk >= 6 then
				A.vsBlock = clamp(((o.blk + 1) / (o.hit + o.blk + 2) - 0.4) * 2, 0, 0.8) * F.iq
			end
		end
		F.adapt[k] = A
		mem.dmg, mem.rounds = {}, mem.rounds + 1
		local parts = {}
		for cat, v in pairs(A) do
			if v >= 0.1 then table.insert(parts, string.format('%s=%.2f', cat, v)) end
		end
		table.sort(parts)
		if #parts > 0 then table.insert(notes, (k:match('([^/]+)%.def$') or k) .. ' ' .. table.concat(parts, ',')) end
	end
	F.mPn = nil -- recompute the matchup with what was learnt
	return table.concat(notes, ' | ')
end

-- ------------------------------------------------------------------ snapshots
-- One read of every fighter per tick; all decisions use these tables.
local function snap(pn)
	if not player(pn) then return nil end
	if isHelper() then return nil end
	local s = {
		pn = pn, id = id(), alive = alive(), life = life(), lifeMax = lifeMax(),
		power = power(), powerMax = powerMax(), ctrl = ctrl(), st = stateNo(), stype = stateType(), mtype = moveType(),
		time = time(), x = screenPosX(), y = screenPosY(), vx = velX(), vy = velY(),
		facing = facing(), side = teamSide(), contact = moveContact(), hit = moveHit(),
		guarded = moveGuarded(), hitpause = hitPauseTime(), guardDist = inGuardDist(),
		lc = localCoordX(), standby = standby(), ai = aiLevel(), backDist = backEdgeBodyDist(),
		maxHits = map('_woo_maxhits'), breaks = map('_woo_breaks'), rh = receivedHits(),
		frontDist = frontEdgeBodyDist(), selectNo = getSelectNo(), name = displayName(),
		attr = hitDefAttr(), anim = anim(),
	}
	s.scale = s.lc / 320 -- local units per world unit
	s.gflag = s.mtype == 'A' and hitDefVar('guardflag') or '' -- H/L/A of the attack in progress
	s.throw = s.attr:find(',%s*%aT') ~= nil
	return s
end

-- ------------------------------------------------------------------ fighter setup
local function modeFor(pn, prof)
	local forced = getCommandLineValue('-woo.p' .. pn)
	if forced == 'own' then return 'coach' end
	if forced == 'pilot' then return 'pilot' end
	if forced == 'overmind' then return 'overmind' end
	if not prof then return 'off' end
	-- no AI of its own: the brain plays it. Any AI at all: keep it and add the overmind layers
	-- (A/B tests showed hand-tuned MUGEN AIs out-fight a from-scratch pilot on their own moves).
	if prof.ai.quality == 'none' and #prof.moves > 0 then return 'pilot' end
	return 'overmind'
end

local function initFighter(s)
	local def = getCharFileName(s.selectNo):gsub('\\', '/')
	local at = def:lower():find('chars[^/]*/') -- chars/... or chars_fix/... (UTF-8 overlays)
	if at then def = def:sub(at) end
	local prof = loadProfile(def)
	local F = {
		-- a slot stays AI-controlled for the whole match: a pilot zeroes its slot's AI level, so the next
		-- Turns member loading into that slot reads ai=0 (user log: Milk mode=off, stood still)
		pn = s.pn, id = s.id, def = def, prof = prof, eligible = s.ai > 0 or W.aiSlot[s.pn] == true,
		macro = nil, hold = 0, holdT = 0, pend = nil, intent = '',
		stats = {}, ban = {}, seen = {}, guardT = 0, decideAt = 0,
		hits = 0, guards = 0, tries = 0, landed = 0,
		chains = prof and prof.chains or {}, recent = {}, stateEnter = 0, lastSt = -1, opp = {},
		focusPn = -1, focusUntil = 0,
	}
	F.mode = F.eligible and modeFor(s.pn, prof) or 'off'
	local tier = prof and prof.tier or 3
	F.T = TIER[clamp(tier, 1, 6)]
	-- personal style: the archetype's values shifted by this character's own identity card
	-- (woo/tools/identity.py: its moveset, its factions' traits, a small per-character offset)
	local idt = prof and prof.identity or {}
	F.Pbase = {}
	for k, v in pairs(PERSONA[prof and prof.persona or 'brawler'] or PERSONA.brawler) do F.Pbase[k] = v end
	for k, d in pairs(idt.style or {}) do
		if F.Pbase[k] and k ~= 'stare' then F.Pbase[k] = math.max(0, F.Pbase[k] + d) end
	end
	F.P = F.Pbase
	F.persona = prof and prof.persona or 'brawler'
	F.lore = prof and prof.lore or nil -- {key, groups, partners, rivals} from woo/lore.txt
	F.stare = F.P.stare
	F.role = idt.roles or {}          -- role -> state: finisher, comeback, antiair, reversal, zoner, opener, launcher, ender, taunt
	F.roleOf = {}
	for r, st in pairs(F.role) do F.roleOf[st] = F.roleOf[st] or r end
	F.sig = idt.sig or {}             -- role -> the author's name for that move
	F.traits = {}
	for _, t in ipairs(idt.traits or {}) do F.traits[t] = true end
	F.hold = idt.hold or (F.persona == 'zoner' and 300 or 130)
	F.mom, F.momN = {}, {}
	-- intelligence: how fast it reads an opponent and counters what beat it (scouting rate, round-2 adaptation)
	F.iq = clamp(0.25 + 0.35 * F.T.skill + (PERSONA_IQ[F.persona] or 0) + (F.traits.disciplined and 0.08 or 0)
		- (F.traits.feral and 0.15 or 0), 0.1, 1.0)
	F.know, F.eAtk, F.mem, F.adapt, F.A = {}, {}, {}, {}, {} -- scouting, enemy attack edges, hit ledger, counters
	F.roundsLost, F.gateUntil = 0, {}
	W.leak[def], W.free[def] = W.leak[def] or {}, W.free[def] or {}
	F.leak, F.free, F.vetoN = W.leak[def], W.free[def], {} -- learned ceilings by cost, meter-free states, recent vetoes
	F.gateJit = (pairRoll(def, 'gate') - 0.5) * 0.10 -- two edits of one character differ a little in when they burst
	F.moveBySt = {}
	for _, m in ipairs(prof and prof.moves or {}) do F.moveBySt[m.st] = F.moveBySt[m.st] or m end
	local scale = (prof and prof.localcoord or 320) / 320
	F.superSt = {}
	-- finisher discipline: the costs of its attack supers (>= 1 bar), cheapest first, with how late each is allowed
	F.supers = {}
	local seenCost = {}
	for _, m in ipairs(prof and prof.moves or {}) do
		if m.super then F.superSt[m.st] = true end
		local c = m.power or 0
		if m.super and c >= 1000 and not seenCost[c] and ((m.hits or 0) > 0 or m.proj or m.helper or m.throw or (m.dmg or 0) > 0) then
			seenCost[c] = true
			table.insert(F.supers, {cost = c, f = SUPER_LEVEL[clamp(math.floor(c / 1000), 1, #SUPER_LEVEL)]})
		end
	end
	table.sort(F.supers, function(a, b) return a.cost < b.cost end)
	F.reachW, F.zoneOK = 40, false
	for _, m in ipairs(prof and prof.moves or {}) do
		if m.charge and m['in'] and #m['in'] > 0 and not m.shadow then F.chargeMove = m break end
	end
	-- powering up is part of how these characters fight (and look) - Dragon Ball most of all
	F.chargeLove = (prof and prof.series == 'dragon ball') and 1.5 or 1.0
	-- frame data the opponent's brain uses to judge safety: our fastest attack, our punish damage
	F.fastest, F.punishDmg = 12, 60
	local dmgs = {}
	for _, m in ipairs(prof and prof.moves or {}) do
		if m.startup and not m.air and m.power == 0 then F.fastest = math.min(F.fastest, m.startup) end
		if (m.dmg or 0) > 0 and m.power <= 1000 then table.insert(dmgs, m.dmg) end
	end
	table.sort(dmgs, function(a, b) return a > b end)
	if #dmgs > 0 then F.punishDmg = (dmgs[1] or 0) + (dmgs[2] or 0) * 0.7 + (dmgs[3] or 0) * 0.5 end
	for _, m in ipairs(prof and prof.moves or {}) do
		if m.power == 0 and not m.super then
			if (m.proj or m.helper) and m.hits ~= nil then F.zoneOK = true end
			if m.reach and not m.air and not m.proj then F.reachW = math.max(F.reachW, math.min(m.reach, 200) / scale) end
		end
	end
	F.tier = tier
	-- Moves the brain can use, grouped once.
	F.moves = {}
	if prof then
		for _, m in ipairs(prof.moves) do
			if (m.pure or (m['in'] and #m['in'] > 0)) and not m.shadow and not m.charge and (m.hits > 0 or m.proj or m.helper or m.super) then
				table.insert(F.moves, m)
			end
		end
	end
	return F
end

-- Apply AI level / input routing / power scaling. `newRound` re-arms the one-shot
-- scaling in woo_brain.zss; an F8 toggle mid-round must not scale twice.
local function applyRound(F, s, newRound, gap)
	player(s.pn)
	if newRound then
		mapSet('_woo_scaled', 0); mapSet('_woo_maxhits', 0); mapSet('_woo_breaks', 0)
		mapSet('_woo_clutch', 0); mapSet('_woo_clutched', 0)
		F.clampSet, F.real, F.gateUntil, F.partnerUntil, F.clutched, F.mPn, F.takeover = nil, nil, {}, nil, false, nil, nil
	end
	local t = F.T
	if F.mode ~= 'off' and POWER_SCALING > 0 then
		local atk, def = 1 + (t.atk - 1) * POWER_SCALING, 1 + (t.def - 1) * POWER_SCALING
		-- second wind: the underdog that lost a round comes back harder: 10% per tier of gap plus up to 25% for how badly
		-- it lost (the winner's life share left), per lost round, max 50%. A flat 5-10% did nothing (ladder 2026-10-21:
		-- underdogs that lost round 1 took round 2 7-12% of the time; they lose it with the winner on ~80% life).
		F.secondWind = 0
		if F.roundsLost > 0 and gap > 0 then
			F.secondWind = math.min(0.50, (0.10 * math.min(gap, 3) + 0.25 * clamp(F.lastDom or 0, 0, 1)) * math.min(F.roundsLost, 2))
			atk, def = atk * (1 + F.secondWind), def * (1 + F.secondWind)
		end
		mapSet('_woo_atk', atk)
		mapSet('_woo_def', def)
	else
		mapSet('_woo_atk', 0)
	end
	if F.mode == 'pilot' and W.enabled then
		mapSet('_woo_on', 1)
		mapSet('_woo_keys', 0)
		setAILevel(0) -- human-path input: unlocks `!ailevel`-gated commands, silences own AI
		local cur = getRemapInput(s.pn)
		if cur ~= DEAD_INPUT_SLOT then W.remap0[s.pn] = cur end -- the real mapping, given back while paused
		remapInput(s.pn, DEAD_INPUT_SLOT)
	elseif F.mode == 'overmind' and W.enabled then
		mapSet('_woo_on', 1)
		mapSet('_woo_keys', 0)
		setAILevel(t.ai) -- its own AI keeps fighting; the brain adds layers on top
	else
		mapSet('_woo_on', 0)
		mapSet('_woo_keys', 0)
		-- coach, or a pilot while the brain is switched off: the character's own AI at tier level
		if F.mode ~= 'off' then setAILevel(t.ai) end
	end
end

-- ------------------------------------------------------------------ geometry
-- dx > 0 means `e` is in front of `s`. Returned in s's local units.
local function relLocal(s, e)
	local dxw = (e.x - s.x) * s.facing
	local dyw = e.y - s.y
	return dxw * s.scale, dyw * s.scale, dxw, dyw
end

local function hitsVertically(s, e, m)
	if not m.ytop then return true end
	-- hitbox world span (y grows downward, feet at s.y)
	local top = s.y + m.ytop / s.scale
	local bot = s.y + m.ybot / s.scale
	local h = BODY_H[e.stype] or 70
	return top < e.y + 4 and bot > e.y - h
end

local function inFrom(m, st)
	for _, r in ipairs(m.from) do
		if st >= r[1] and st <= r[2] then return true end
	end
	return false
end

-- ------------------------------------------------------------------ move scoring
local function classOf(m)
	if m.super then return 'super' end
	if m.throw then return 'throw' end
	if m.proj or m.helper then return 'proj' end
	if m.power > 0 then return 'special' end
	return 'normal'
end

local function reachOf(m)
	if m.reach then return m.reach end
	if m.proj then return 1500 end
	if m.helper then return 120 end
	return 45
end

-- Expected value of throwing move m at enemy e right now (nil = not possible).
local function scoreMove(F, s, e, m, cancel, team)
	local now_ = F.now
	if F.ban[m.st] then return nil end
	if m.power > s.power then return nil end
	local air = s.stype == 'A'
	if m.stype == 'A' and not air then return nil end
	if m.stype ~= 'A' and m.stype ~= 'any' and air then return nil end
	local first = m['in'] and m['in'][1]
	local holdsDown = (first and first[1] % 4 >= 2) or (m.pure and m.stype == 'C')
	if m.stype == 'C' and s.stype ~= 'C' and not holdsDown then return nil end
	if m.stype == 'S' and s.stype == 'C' then return nil end
	if s.stype == 'C' and not holdsDown then return nil end -- crouched: only moves that hold down
	if (m.proj or m.helper) and F.now - (F.lastProj or -999) < 50 + (1.6 - F.P.proj) * 40 then return nil end
	if cancel then
		if #m.from == 0 or not inFrom(m, s.st) then return nil end
		if m.contact and s.contact == 0 then return nil end
	else
		if not (m.ctrl and s.ctrl) then return nil end
	end

	local dx, dy = relLocal(s, e)
	local body = BODY_HALF * s.scale
	if dx < -body then return nil end -- behind
	local startup = m.startup or 8
	local learned = F.reachAdj and F.reachAdj[m.st]
	if learned and dx > learned + body then return nil end -- whiffed from here before
	if m.ai_x then
		-- the character author's own range window for this move (p2bodydist x)
		local bodyD = dx - 2 * body
		if bodyD < m.ai_x[1] - 8 or bodyD > m.ai_x[2] then return nil end
	else
		local travel = (m.lunge or 0) * math.min(startup + 2, 20)
		if dx > reachOf(m) + travel + body then return nil end
		if not (m.proj or m.helper) and not hitsVertically(s, e, m) then return nil end
	end

	local P = F.P
	local cls = classOf(m)
	local dmg = (m.dmg and m.dmg > 0) and m.dmg or (cls == 'super' and 150 or (cls == 'proj' and 45 or 30))

	-- chance the move connects given what the enemy is doing
	local p
	local eGuard = e.st >= 120 and e.st <= 155
	if e.mtype == 'H' then
		p = (e.stype == 'L') and 0.05 or (e.stype == 'A' and 0.75 or 0.92)
	elseif eGuard then
		p = m.throw and 0.85 or 0.12
	elseif e.stype == 'L' then
		p = 0.02
	elseif e.mtype == 'A' then
		-- just blocked it: their recovery is ours to punish with something fast
		local justBlocked = now_ - (F.guardUntil or -99) < 10 and startup <= 10
		p = m.invuln and 0.85 or (justBlocked and 0.75 * P.punish + 0.1 or 0.30)
	elseif not e.ctrl then
		p = 0.70 * P.punish + 0.1 -- landing / recovery: punish window
	else
		-- neutral: how often has *this* opponent blocked our attacks so far?
		local o = F.opp[e.pn] or {hit = 0, blk = 0}
		-- what it knows of this opponent counts as a few observations (how readily it guards)
		local kn0, Fe0 = knowOf(F, e), W.F[e.pn]
		local blockRate = (o.blk + 1 + 3 * kn0 * (Fe0 and Fe0.P and Fe0.P.guard or 0.6)) / (o.hit + o.blk + 2 + 3 * kn0)
		p = (1 - blockRate) * 0.9 * (1.25 - math.min(startup, 30) / 40)
	end
	if m.throw and not eGuard and e.mtype ~= 'H' then p = p * (e.stype == 'A' and 0 or 0.8) end
	-- the author's AI used this move only against certain enemy states (anti-air, combo, OTG)
	local function has(t, v) for _, x in ipairs(t) do if x == v then return true end end return false end
	if m.ai_p2s and #m.ai_p2s > 0 then p = p * (has(m.ai_p2s, e.stype) and 1.3 or 0.25) end
	if m.ai_p2m and #m.ai_p2m > 0 then p = p * (has(m.ai_p2m, e.mtype) and 1.3 or 0.25) end

	local w = 1
	if cls == 'proj' then
		w = P.proj * (dx > 120 * s.scale and 1.4 or 0.7)
	elseif cls == 'super' then
		w = P.super * ((e.mtype == 'H' or not e.ctrl) and 1.6 or 0.5)
		if e.life <= dmg * 1.2 then w = w * 2.0 end -- finisher
		if team.allySuper then w = w * (1 + 2 * (team.bond or 0.5)) end -- chain onto a teammate's super
	elseif cls == 'special' then
		w = 0.9 + P.agg * 0.3
	elseif cls == 'normal' then
		w = 0.8 + P.agg * 0.4 - (P.range > 0.6 and dx > 60 * s.scale and 0.4 or 0)
	end
	if m.air then w = w * (0.7 + P.jump) end
	if m.throw then w = w * (1 + 1.5 * (F.A.vsBlock or 0)) end -- it blocks everything: mix in throws (learnRound)
	-- signature roles: the character's own move for the moment that defines it
	local role = F.roleOf and F.roleOf[m.st]
	if role == 'finisher' and e.life <= dmg * 1.3 then w = w * 2.5
	elseif role == 'comeback' and s.life < s.lifeMax * 0.3 then w = w * 2.0
	elseif role == 'antiair' and e.stype == 'A' and e.mtype ~= 'H' then w = w * 2.2
	elseif role == 'reversal' and now_ - (F.guardUntil or -99) < 8 then w = w * 2.0
	elseif role == 'zoner' and dx > 150 * s.scale then w = w * 1.4
	elseif role == 'opener' and e.ctrl and e.mtype == 'I' then w = w * 1.3
	end
	if m.launch and team.allyNear then w = w * 1.4 end -- set up the partner's follow-up
	if cls == 'super' and F.mTag == 'RIVAL' and e.power >= 1000 then w = w * 1.5 end -- rivals clash supers
	if m.shadow then w = w * 0.15 end -- an earlier State -1 entry usually steals this input
	if cancel then w = w * (0.6 + P.combo) end
	if team.focus and team.focus.pn == e.pn then w = w * 1.25 end
	if team.relay and team.relay.pn == e.pn then w = w * (1.5 + 0.8 * (team.pbond or 0)) end
	if team.rescue and team.rescue.pn == e.pn then w = w * 1.6 end

	-- frame-data risk: if it whiffs or is blocked, can they punish before we recover?
	local recovery = (m.total or 30) - startup - (m.active or 4)
	local Fe = W.F[e.pn]
	local kn = knowOf(F, e) -- a stranger's speed and punish power are guesses until it has been watched
	local theirFastest = 10 + ((Fe and Fe.fastest or 10) - 10) * kn
	local punishable = recovery > theirFastest + 2 and not (m.proj and dx > 120 * s.scale)
	local risk = 0
	if e.mtype ~= 'H' and punishable then
		risk = (1 - p) * (60 + ((Fe and Fe.punishDmg or 60) - 60) * kn) * P.risk * (0.5 + 0.5 * F.T.skill)
	end

	-- learning: moves that keep failing to come out lose weight
	local st = F.stats[m.st]
	if st and st.tries >= 2 then w = w * (0.3 + 0.7 * st.ok / st.tries) end
	-- variety: a fighter that repeats one button looks like a bot
	for _, r in ipairs(F.recent) do
		if r == m.st then w = w * 0.6 end
	end

	return dmg * p * w - risk
end

-- ------------------------------------------------------------------ macros
local function startMacro(F, seq, intent, now)
	F.macro = {seq = seq, i = 1, t = 0}
	F.intent = intent
	F.lastIntentAt = now
end

local function macroBits(F)
	local mc = F.macro
	if not mc then return 0 end
	local step = mc.seq[mc.i]
	if not step then F.macro = nil return 0 end
	mc.t = mc.t + 1
	local b = step[1]
	if mc.t >= step[2] then
		mc.i = mc.i + 1
		mc.t = 0
		if mc.i > #mc.seq then F.macro = nil end
	end
	return b
end

-- m: a catalog move, or a chain edge {to=, in=} (both carry an input macro)
local function useMove(F, s, m, intent, now)
	local target = m.st or m.to
	-- overmind: a move whose input route failed once (the old AI var keeps its own AI pressing buttons
	-- through the burst) is entered directly next time, as hand-written MUGEN AIs do
	local direct = m.pure or (F.viaDirect and F.viaDirect[target])
	if direct and (m.st or m.to) then
		-- 0-frame entry, like a hand-written MUGEN AI: the route has no condition the
		-- brain hasn't already checked (command, power, statetype, ctrl/cancel window)
		F.direct, F.intent = target, intent
		-- overmind: its own AI's ChangeState in -1 overrides the entry on the same tick (fail logs:
		-- 1700 -> 600), so the AI is held for a few ticks like a burst
		if F.mode == 'overmind' then F.holdAI = now + 4 end
	else
		startMacro(F, m['in'], intent, now)
		if F.mode == 'overmind' then F.burst = true end
	end
	local len = 0
	for _, st in ipairs(m['in']) do len = len + st[2] end
	F.pend = {st = target, deadline = now + len + 6, m = m,
		ctx = string.format('st=%d ctrl=%s stype=%s mtype=%s t=%d', s.st, tostring(s.ctrl), s.stype, s.mtype, s.time)}
	F.tries = F.tries + 1
	if m.proj or m.helper then F.lastProj = now end
	local st = F.stats[target] or {tries = 0, ok = 0}
	st.tries = st.tries + 1
	F.stats[target] = st
	table.insert(F.recent, 1, target)
	F.recent[6] = nil
end

-- ------------------------------------------------------------------ team blackboard
local function teamBoard(S, side)
	local allies, enemies = {}, {}
	for _, s in pairs(S) do
		if s.alive and not s.standby then
			if s.side == side then table.insert(allies, s) else table.insert(enemies, s) end
		end
	end
	local B = {allies = allies, enemies = enemies, comboedBy = {}, rescueFrom = nil, allySuper = {}}
	-- who is comboing which enemy: nearest ally in attack with contact
	for _, e in ipairs(enemies) do
		if e.mtype == 'H' then
			local best, bd = nil, 1e9
			for _, a in ipairs(allies) do
				local d = math.abs(a.x - e.x)
				if a.mtype == 'A' and d < bd then best, bd = a, d end
			end
			B.comboedBy[e.pn] = best
		end
	end
	-- an ally in hitstun: who is beating them up
	for _, a in ipairs(allies) do
		if a.mtype == 'H' and a.stype ~= 'L' then
			local best, bd = nil, 1e9
			for _, e in ipairs(enemies) do
				local d = math.abs(a.x - e.x)
				if e.mtype == 'A' and d < bd then best, bd = e, d end
			end
			if best then B.rescueFrom = {victim = a, attacker = best} end
		end
		local Fa = W.F[a.pn]
		if Fa and Fa.superSt[a.st] and a.mtype == 'A' then
			B.allySuper[a.pn] = true
		end
	end
	return B
end

-- Lore bonds between two fighters (woo/lore.txt): canon partners fight as one, rivals
-- compete even as allies, a shared faction coordinates, strangers barely do.
local function bond(Fa, Fb)
	local a, b = Fa and Fa.lore, Fb and Fb.lore
	if not a or not b then return 0.25, '' end
	if a.key == b.key then return 0.8, 'MIRROR' end
	if listHas(a.partners, b.key) or listHas(b.partners, a.key) then return 1.0, 'PARTNERS' end
	if listHas(a.rivals, b.key) or listHas(b.rivals, a.key) then return 0.7, 'RIVALS' end
	for _, g in ipairs(a.groups or {}) do
		if listHas(b.groups, g) then return 0.6, g:upper() end
	end
	return 0.25, ''
end

-- Team events (rescue a teammate, relay a combo) roll once per event, so a fighter
-- doesn't flip between helping and not helping every frame.
local function teamRoll(F, key, allyPn)
	if F.teamKey ~= key then
		local b = bond(F, W.F[allyPn])
		-- canon partners (Naruto and Minato, Tsunade and Tobirama) don't hesitate: they come in almost every time
		F.teamKey, F.teamYes = key, rnd() < math.min(0.98, F.P.team * (0.4 + 0.9 * b) + (b >= 1 and 0.25 or 0))
	end
	return F.teamYes
end

-- ------------------------------------------------------------------ finisher discipline
-- Supers are finishers, not openers (user: Pain used Shinra Tensei on a Zoro at 3/4 life). Every brain-driven
-- fighter, its own AI included, spends a bar of meter only once the fight has earned it: an enemy is nearly
-- beaten, the fighter itself is in trouble, a canon partner is relaying a combo, a teammate's super is landing.
-- Mechanism: a character's AI fires a super whenever `power >= cost`, so while the gate is shut the power bar is
-- held one point under the cost of the cheapest shut level and what the fighter earns above that is banked;
-- the bank is paid back the moment the gate opens. No character file is touched.
local function gateOpenAt(F)
	local at = 0.24 + 0.20 * clamp(F.P.super, 0.3, 1.3) -- persona/identity appetite: berserkers burst early, calm ones wait
	if F.traits.feral then at = at + 0.06 end
	if F.traits.proud then at = at + 0.03 end
	if F.traits.aloof then at = at - 0.08 end
	if F.traits.disciplined then at = at - 0.04 end
	if F.mTag == 'OUTCLASSED' then at = at + 0.15 -- the underdog bursts earlier: it needs the swing
	elseif F.mTag == 'TOYING' then at = at - 0.10
	elseif F.mTag == 'RIVAL' then at = at + 0.12 end
	at = at + 0.06 * math.min(F.roundsLost, 2) -- stung once: stops saving it
	return clamp(at + F.gateJit, 0.12, 0.85)
end

local function gateOpen(F, s, B, now, lv)
	local at = gateOpenAt(F) * lv.f
	for _, e in ipairs(B.enemies) do
		if e.life <= e.lifeMax * at and (#B.enemies == 1 or math.abs(e.x - s.x) < 450) then return 'foe P' .. e.pn end
	end
	if s.life <= s.lifeMax * 0.34 * math.sqrt(lv.f) then return 'self' end -- in trouble: nothing left to save it for
	-- team openings, 1-bar supers only (never the big finishers), canon partners only, and only once an enemy is hurt
	-- (2v2 test: Uchiha allies opened supers at 90% for each other all round)
	if lv.f >= 1 then
		local hurt = false
		for _, e in ipairs(B.enemies) do
			if e.life <= e.lifeMax * math.min(0.8, at * 1.6) then hurt = true end
		end
		if hurt then
			if (F.partnerUntil or 0) > now then return 'relay' end
			for apn in pairs(B.allySuper) do -- (the board lists the fighter itself too: its own super is no reason)
				if apn ~= s.pn and bond(F, W.F[apn]) >= 0.9 then return 'ally super P' .. apn end
			end
		end
	end
	return nil
end

local function gatePower(F, s, B, now, intro)
	local ceiling
	F.powBefore, F.closedBefore, F.closedAfter = F.powAfter, F.closedAfter or {}, {} -- last tick's, for superWatch
	for _, lv in ipairs(F.supers) do
		-- intro: always shut (round 2+ intros still show the life the fighter was KO'd with, which read as 'in trouble')
		local why = not intro and gateOpen(F, s, B, now, lv)
		if why then
			if (F.gateUntil[lv.cost] or 0) < now then
				F.momN['SUPER OPEN'] = (F.momN['SUPER OPEN'] or 0) + 1
				local low = 2
				for _, e in ipairs(B.enemies) do low = math.min(low, e.life / math.max(1, e.lifeMax)) end
				log(string.format('gate OPEN P%d cost=%d why=%s foe_low=%d%% at=%d%% self=%d%% t=%d', s.pn, lv.cost, why,
					math.floor(low * 100), math.floor(gateOpenAt(F) * lv.f * 100), math.floor(100 * s.life / math.max(1, s.lifeMax)), now))
			end
			F.gateUntil[lv.cost] = now + 90 -- stays open 1.5 s after the reason ends
		end
		local shut = intro or (F.gateUntil[lv.cost] or 0) < now
		F.closedAfter[lv.cost] = shut
		-- every shut level counts: a learned ceiling of a dearer super can be lower than the cheap one's (Hijikata's
		-- 3-bar ultimate starts at ~200 power, his 1-bar clamp is 499)
		if shut then ceiling = math.min(ceiling or 1e9, lv.cost - 1, F.leak[lv.cost] or 1e9) end
	end
	-- A ctrl-less idle state that runs by itself for a second (ki charge until full, power-up aura) waits for
	-- power the clamp never grants and would hold both fighters forever: let it run, clamp again once it ends.
	if s.ctrl or s.mtype ~= 'I' then F.idleSt = nil
	elseif F.idleSt ~= s.st then F.idleSt, F.idleSince = s.st, now end
	if not intro and F.idleSt and now - F.idleSince >= 60 then ceiling = nil end -- (intro poses are ctrl-less idle too)
	-- A charge state its AI cannot leave holds the fighter at full power until hit (Zoro's power charge ends only when
	-- ailevel = 0 and power = 3000: entered by the brain's CHARGE KI burst, it stalled whole rounds). At full power the AI
	-- is held off for a few ticks so the character's own exit fires, as it does for a human. Nothing forces the state
	-- to end: Hakari's Jackpot (12.5 s) and Titan Colossal's titan form are legitimate long ctrl-less states.
	if not intro and F.idleSt and (F.real or s.power) >= s.powerMax then
		F.fullSince = F.fullSince or now
		if now - F.fullSince >= 20 then F.holdAI = now + 3 end
	else
		F.fullSince = nil
	end
	local real = s.power
	if F.clampSet ~= nil then real = math.max(0, (F.real or s.power) + (s.power - F.clampSet)) end
	if ceiling and real > ceiling then
		F.real, F.clampSet = real, ceiling
		if s.power ~= ceiling then setPower(ceiling) end
		s.power = ceiling
	elseif F.clampSet ~= nil then
		F.clampSet, F.real = nil, nil
		real = math.min(real, s.powerMax)
		if real ~= s.power then setPower(real) end
		s.power = real
	end
	s.powerReal = F.real or s.power -- what it has really earned: ki-charging decisions look at this
	F.powAfter = s.power
end

-- Fight over, KO'd or brain off: give the banked power back.
local function releasePower(F, s)
	if F.clampSet == nil then return end
	local real = math.min(math.max(0, (F.real or s.power) + (s.power - F.clampSet)), s.powerMax)
	F.clampSet, F.real = nil, nil
	setPower(real)
end

-- Fighting spirit, once per round at 25% life: a small attack/defence swing, bigger for the underdog and for the
-- loyal and proud. Applied by woo_brain.zss (maps _woo_clutch / _woo_cf).
local function clutch(F, s, S)
	if F.clutched or s.life > s.lifeMax * 0.25 then return end
	F.clutched = true
	local gap = 0
	for pn2, F2 in pairs(W.F) do
		if S[pn2] and S[pn2].side ~= s.side then gap = math.max(gap, (F2.tier or 3) - F.tier) end
	end
	mapSet('_woo_cf', 0.04 + 0.04 * clamp(gap, 0, 3) + ((F.traits.loyal or F.traits.proud) and 0.03 or 0))
	mapSet('_woo_clutch', 1)
	F.momN.CLUTCH = (F.momN.CLUTCH or 0) + 1
end

-- Matchup: how this character fights *this* opponent. Rivals go all in (supers, taunts); an outclassed
-- fighter turns patient and punishes; a far stronger one toys with its prey; zoners get rushed and
-- rushdowns get kept out. Recomputed whenever the focus target changes.
local function matchup(F, Fe, e)
	local P = {}
	for k, v in pairs(F.Pbase) do P[k] = v end
	if not Fe then return P, '' end
	local tag = ''
	local know = e and knowOf(F, e) or 0
	local dt = (Fe.tier or 3) - (F.tier or 3)
	if dt >= 2 then
		P.guard, P.risk, P.punish, P.agg = P.guard + 0.12, P.risk + 0.25, P.punish + 0.10, P.agg - 0.10
		tag = 'OUTCLASSED'
	elseif dt <= -2 then
		P.taunt, P.risk = P.taunt + 0.15, math.max(0, P.risk - 0.15)
		tag = 'TOYING'
	end
	-- reading the opponent's style takes some knowledge of it (a stranger has to watch first)
	local eP = know >= 0.3 and (Fe.Pbase or Fe.P) or {}
	if (eP.range or 0) > 0.6 and P.range < 0.5 then
		P.dash, P.agg = P.dash + 0.2, P.agg + 0.1
		if tag == '' then tag = 'RUSH ZONER' end
	elseif (eP.agg or 0) > 0.8 and P.range > 0.6 then
		P.guard, P.proj = P.guard + 0.08, P.proj + 0.2
		if tag == '' then tag = 'KEEP AWAY' end
	end
	if F.traits.ruthless and (Fe.tier or 3) <= (F.tier or 3) then P.punish, P.combo = P.punish + 0.1, P.combo + 0.05 end
	if F.lore and Fe.lore and (listHas(F.lore.rivals, Fe.lore.key) or listHas(Fe.lore.rivals, F.lore.key)) then
		P.agg, P.super, P.taunt = P.agg + 0.15, P.super + 0.25, P.taunt + 0.1
		tag = 'RIVAL'
	end
	-- what this opponent already taught it (learnRound): counter the way it was beaten
	local A = F.adapt[Fe.def or '']
	if A then
		local pr, ai, th, su, me = A.proj or 0, A.air or 0, A.throw or 0, A.super or 0, A.melee or 0
		P.dash, P.agg, P.range = P.dash + 0.35 * pr, P.agg + 0.25 * pr, P.range - 0.25 * pr + 0.10 * me + 0.10 * th
		P.jump = P.jump + 0.20 * pr - 0.30 * ai
		P.guard, P.risk, P.punish = P.guard + 0.20 * su + 0.15 * me, P.risk + 0.30 * su, P.punish + 0.25 * me
	end
	for k, v in pairs(P) do if v < 0 then P[k] = 0 end end
	return P, tag, A
end

local function teamEvent(F, why)
	local k = why and why:match('^(%u+)')
	if k and (k == 'RELAY' or k == 'RESCUE') and why ~= F.lastWhy then F.momN[k] = (F.momN[k] or 0) + 1 end
	if k == 'RELAY' then -- relaying a canon partner's combo: its super may finish the pair's combo (gateOpen)
		local apn = tonumber(why:match('P(%d+)'))
		if apn and bond(F, W.F[apn]) >= 0.9 then F.partnerUntil = (F.now or 0) + 180 end
	end
	F.lastWhy = why
end

local function applyMatchup(F, target)
	if not target then return end
	local kb = math.floor(knowOf(F, target) * 4) -- redo the read when its knowledge of the opponent crosses a step
	if target.pn ~= F.mPn or kb ~= F.mKnow then
		local A
		F.P, F.mTag, A = matchup(F, W.F[target.pn], target)
		F.A = A or {}
		F.mPn, F.mKnow = target.pn, kb
	end
end

local MOMENT_CD = {finisher = 240, comeback = 900, antiair = 90, reversal = 120, zoner = 150, taunt = 600, launcher = 200}

local function chooseFocus(F, s, B, now)
	if B.rescueFrom and B.rescueFrom.victim.pn ~= s.pn and
		teamRoll(F, 'R' .. B.rescueFrom.victim.pn .. ':' .. B.rescueFrom.attacker.pn, B.rescueFrom.victim.pn) then
		return B.rescueFrom.attacker, 'RESCUE P' .. B.rescueFrom.victim.pn
	end
	for epn, by in pairs(B.comboedBy) do
		if by and by.pn ~= s.pn and teamRoll(F, 'L' .. epn .. ':' .. by.pn, by.pn) then
			for _, e in ipairs(B.enemies) do
				if e.pn == epn then return e, 'RELAY P' .. by.pn end
			end
		end
	end
	-- persona target, kept for a while (no frame-to-frame target swapping)
	for _, e in ipairs(B.enemies) do
		if e.pn == F.focusPn and now < F.focusUntil then return e, '' end
	end
	local best, bv = nil, -1e9
	for _, e in ipairs(B.enemies) do
		local d = math.abs(e.x - s.x)
		local v = -d
		if F.persona == 'tactician' or F.persona == 'effortless' then v = -e.life - d * 0.5 end
		if F.persona == 'warrior' then
			local Fe = W.F[e.pn]
			v = (Fe and Fe.tier or 3) * 200 - d -- seeks the strongest opponent
		end
		if F.persona == 'zoner' then v = -d * 0.3 - e.life * 0.2 end
		local Fe = W.F[e.pn]
		if F.lore and Fe and Fe.lore and listHas(F.lore.rivals, Fe.lore.key) then
			v = v + 400 + F.P.agg * 400 -- canon rival: settle it
		end
		if v > bv then best, bv = e, v end
	end
	if best then F.focusPn, F.focusUntil = best.pn, now + 45 + math.floor(rnd() * 45) end
	return best, ''
end

-- An enemy that has sat in one state for 6 s is not attacking, whatever the engine's guard-distance flag says: a charge
-- aura keeps spawning HitDef helpers (Shadow V2's power charge made Gohan_Beast guard it forever: stalled rounds).
local function staleThreat(B, s)
	for _, e in ipairs(B.enemies) do
		if (e.time or 0) < 360 and math.abs(e.x - s.x) < 300 then return false end
	end
	return true
end

-- What this fighter has *noticed* about enemy e: its state as of `react` frames ago
-- (positions stay current). Sharper tiers notice sooner.
local function perceive(e, react, now)
	local h = W.hist[e.pn] and W.hist[e.pn][(now - react) % 64]
	if not h or h.t ~= now - react then return e end
	local pe = {}
	for k, v in pairs(e) do pe[k] = v end
	pe.mtype, pe.stype, pe.ctrl, pe.st, pe.gflag, pe.throw = h.mtype, h.stype, h.ctrl, h.st, h.gflag, h.throw
	return pe
end

local function setPlan(F, kind, bits_, dur, intent, now, target)
	F.plan = {kind = kind, bits = bits_, untilT = now + math.floor(dur), intent = intent, target = target}
	F.intent = intent
	return bits_
end

-- Did the move we asked for come out? Learn it (ban moves that never do) and log failures.
local function confirmMove(F, s, now)
	if F.pend then
		if s.st == F.pend.st or (s.mtype == 'A' and s.st > F.pend.st and s.st < F.pend.st + 10) then
			F.outcome = {st = s.st, at = now, m = F.pend.m}
			local st = F.stats[F.pend.st]
			st.ok = st.ok + 1
			F.cameOut = (F.cameOut or 0) + 1
			F.pend = nil
		elseif now > F.pend.deadline then
			local st = F.stats[F.pend.st]
			if s.mtype ~= 'H' then
				F.viaDirect = F.viaDirect or {}
				F.viaDirect[F.pend.st] = true
			end
			if st.tries >= 3 and st.ok == 0 then F.ban[F.pend.st] = true end
			if W.verbose and (F.failLog or 0) < 12 then
				F.failLog = (F.failLog or 0) + 1
				log(string.format('   fail P%d %d from %s -> st=%d ctrl=%s mtype=%s',
					s.pn, F.pend.st, F.pend.ctx, s.st, tostring(s.ctrl), s.mtype))
			end
			F.pend = nil
		end
	end
end

-- ------------------------------------------------------------------ the decision
local function decide(F, s, S, B, now)
	local P, T = F.P, F.T
	F.now = now
	if s.st ~= F.lastSt then F.lastSt, F.stateEnter = s.st, now end

	confirmMove(F, s, now)
	if s.hit > 0 and (F.prevHit or 0) == 0 then F.landed = F.landed + 1 end -- moveHit rising edge
	-- whiff: our move ended without touching anyone -> it reaches less far than its hitbox says
	-- (hitboxes that only exist on some frames, moves that hit high): use it from closer this match
	local oc = F.outcome
	if oc and oc.m and s.hit == 0 and s.guarded == 0 and now - oc.at > 2 and s.st ~= oc.st
		and not (oc.m.proj or oc.m.helper) and s.mtype ~= 'H' then
		F.reachAdj = F.reachAdj or {}
		F.reachAdj[oc.st] = math.max(20, (F.reachAdj[oc.st] or reachOf(oc.m)) * 0.8)
		F.whiffs = (F.whiffs or 0) + 1
		F.outcome = nil
	end
	-- opponent model: did our last move get blocked or land? (attributed to the nearest enemy)
	if F.outcome and (s.hit > 0 or s.guarded > 0) then
		local near, nd = nil, 1e9
		for _, e in ipairs(B.enemies) do
			local d = math.abs(e.x - s.x)
			if d < nd then near, nd = e, d end
		end
		if near then
			local o = F.opp[near.pn] or {hit = 0, blk = 0}
			if s.hit > 0 then o.hit = o.hit + 1 else o.blk = o.blk + 1 end
			F.opp[near.pn] = o
		end
		if F.outcome.m and F.reachAdj and F.reachAdj[F.outcome.st] then -- it connected: trust it a bit more again
			F.reachAdj[F.outcome.st] = math.min(reachOf(F.outcome.m), F.reachAdj[F.outcome.st] * 1.1)
		end
		F.outcome = nil
	end
	if F.prevLife and s.life < F.prevLife then
		F.lostBy = F.lostBy or {}
		local k = F.hitKey or 'other'
		F.lostBy[k] = (F.lostBy[k] or 0) + (F.prevLife - s.life)
	end
	F.prevLife = s.life
	if s.mtype == 'H' and F.prevMtype ~= 'H' then -- got hit: remember what we were doing (diagnostics)
		local k = (F.prevMtype == 'A' and 'attacking' or (F.intent ~= '' and F.intent:match('^%S+') or 'idle'))
		F.hitBy = F.hitBy or {}
		F.hitBy[k] = (F.hitBy[k] or 0) + 1
		F.hitKey = k
	end
	if s.mtype ~= 'H' and s.stype ~= 'L' then F.hitKey = nil end
	F.prevMtype = s.mtype
	F.prevHit = s.hit

	if F.macro and F.charging then
		local close = false
		for _, e in ipairs(B.enemies) do
			if math.abs(e.x - s.x) < 100 or e.mtype == 'A' and math.abs(e.x - s.x) < 160 then close = true end
		end
		if close or s.guardDist or s.mtype == 'H' then F.macro, F.charging = nil, false end
	end
	if not F.macro then F.charging = false end
	if F.macro then return macroBits(F) end
	-- the input is in the buffer: let the move start before pressing anything else
	-- (a follow-up input in this window becomes a different move via the char's own chains)
	if F.pend and F.pend.st == (F.pend.m.st or F.pend.m.to) and now <= F.pend.deadline then return 0 end
	if s.mtype == 'H' or s.stype == 'L' then
		F.plan, F.intent = nil, ''
		return 0
	end

	local target, why = chooseFocus(F, s, B, now)
	applyMatchup(F, target)
	teamEvent(F, why)
	if not target then return 0 end
	-- nearest enemy in front is who attacks will actually hit
	local front, fd = nil, 1e9
	for _, e in ipairs(B.enemies) do
		local dx = relLocal(s, e)
		if dx > -BODY_HALF * s.scale and dx < fd then front, fd = e, dx end
	end
	front = front or target
	front = perceive(front, rx(F, front), now)
	local team = {
		focus = target, relay = why:sub(1, 5) == 'RELAY' and target or nil,
		rescue = why:sub(1, 6) == 'RESCUE' and target or nil,
		allySuper = next(B.allySuper) ~= nil,
	}
	for _, a in ipairs(B.allies) do
		if a.pn ~= s.pn and math.abs(a.x - target.x) < 160 then team.allyNear = true end
	end
	for apn in pairs(B.allySuper) do team.bond = math.max(team.bond or 0, (bond(F, W.F[apn]))) end
	if team.relay or team.rescue then -- how close the partner it is helping really is
		local apn = tonumber(why:match('P(%d+)'))
		team.pbond = apn and bond(F, W.F[apn]) or 0
	end

	-- 0) round opening: a beat of stare-down before anyone moves (longer for the calm ones)
	local pressed = false
	for _, e in ipairs(B.enemies) do
		if math.abs(e.x - s.x) < 140 or e.mtype == 'A' then pressed = true end
	end
	if not pressed and now - (W.fightStart or now) < F.stare then
		return setPlan(F, 'hold', 0, F.stare, 'STARE DOWN', now, target.pn)
	end

	-- 1) defense: read the attack that is coming (as noticed `react` frames ago) and answer
	-- it the way it can actually be stopped: high/low block, or get out of a throw/unblockable
	local inc, incD = nil, 1e9
	for _, e in ipairs(B.enemies) do
		local pe = perceive(e, rx(F, e), now)
		local Fe = W.F[e.pn]
		local d = math.abs(e.x - s.x)
		if pe.mtype == 'A' and (e.time or 0) < 360 and d < reachSeen(F, Fe, e) + 60 and d < incD then inc, incD = pe, d end
	end
	local threatened = ((s.guardDist and not staleThreat(B, s)) or inc ~= nil) and (s.ctrl or (s.st >= 120 and s.st <= 155))
	if threatened then
		F.guardT = F.guardT + 1
		-- one guard roll per threat; a failed roll means it eats this one
		if F.guardRoll == nil and F.guardT >= T.react * 0.5 then
			-- calibrated so tier 3 blocks about as well as a stock MUGEN AI at level 8
			-- the familiar know the timing of the attack coming (+0.18 at full knowledge)
			F.guardRoll = rnd() < math.min(0.97, (0.55 + 0.45 * T.skill) * (0.6 + 0.4 * P.guard) + 0.18 * (inc and knowOf(F, inc) or 0))
			if F.guardRoll then F.guards = F.guards + 1 end
		end
		if F.guardRoll then
			local g = inc and inc.gflag or 'HL'
			if inc and (inc.throw or g == '') and s.ctrl and s.stype ~= 'A' then
				F.guardRoll = nil
				startMacro(F, {{bits('U', 'B'), 3}}, inc.throw and 'ESCAPE THROW' or 'EVADE UNBLOCKABLE', now)
				return macroBits(F)
			end
			local low = g:find('L') and not g:find('H')
			if not inc then low = front.stype == 'C' end
			F.guardBits = low and bits('D', 'B') or BIT.B
			F.guardUntil = now + 6 -- stay in stance through the gaps of a string
			-- step straight into the common guard state (stand 130 / crouch 131 / air 132),
			-- as hand-written MUGEN AIs do; holding back keeps it up
			if s.ctrl and (s.st < 120 or s.st > 155) then
				F.direct = s.stype == 'A' and 132 or (low and 131 or 130)
			end
			F.plan, F.intent = nil, low and 'GUARD LOW' or 'GUARD'
			return F.guardBits
		end
	else
		F.guardT, F.guardRoll = 0, nil
	end

	-- 2) combo: our move connected and the enemy is reeling
	local cancel = s.mtype == 'A' and s.contact > 0 and s.hitpause == 0 and (front.mtype == 'H' or s.guarded > 0)
	if cancel and rnd() > math.min(0.98, T.skill * P.combo + 0.15 * (team.pbond or 0)) then cancel = false end

	-- 2b) chain: this attack state continues on a button press (a,a,a -> b -> special)
	local edges = s.mtype == 'A' and not F.pend and F.chains[tostring(s.st)]
	if edges and F.chainTried ~= s.st .. ':' .. F.stateEnter and
		(front.mtype == 'H' or s.contact > 0 or rnd() < 0.02 + P.agg * 0.03) then
		local best, bv = nil, 0
		for _, e in ipairs(edges) do
			if s.time >= e.after and e.power <= s.power and not F.ban[e.to] and (not e.contact or s.contact > 0)
				and not (e.pure and s.hitpause > 0) then
				local v = (e.dmg > 0 and e.dmg or 20) * (e.super and P.super * 1.5 or 1) * (0.5 + rnd())
				for _, r in ipairs(F.recent) do
					if r == e.to then v = v * 0.75 end
				end
				if v > bv then best, bv = e, v end
			end
		end
		if best then
			F.chainTried = s.st .. ':' .. F.stateEnter
			if rnd() < T.skill * P.combo + 0.1 then
				useMove(F, s, best, 'CHAIN ' .. s.st .. '>' .. best.to, now)
				return macroBits(F)
			end
		end
	end

	-- 3) attack choice, at a human cadence (not every frame) - but at once when the
	-- opponent steps into our reach
	local _, _, dxwF = relLocal(s, front)
	local inReach = math.abs(dxwF) <= F.reachW + BODY_HALF
	if inReach and not F.wasInReach then F.decideAt = now end
	F.wasInReach = inReach
	if (s.ctrl or cancel) and (cancel or now >= F.decideAt) then
		local best, bv = nil, 0
		for _, e in ipairs(B.enemies) do
			local pe = perceive(e, rx(F, e), now)
			for _, m in ipairs(F.moves) do
				local v = scoreMove(F, s, pe, m, cancel, team)
				if v and inc and not cancel and not m.invuln and incD < 80 then v = nil end
				if v then
					v = v * (1 + (rnd() - 0.5) * (1.2 - T.skill)) -- weaker fighters choose noisier
					if v > bv then best, bv = m, v end
				end
			end
		end
		-- aggression gate: patient personas let marginal neutral chances go
		local neutral = not cancel and front.mtype == 'I' and front.ctrl
		-- an author range window is a confident read: take it more often
		if best and (not neutral or rnd() < 0.30 + P.agg * 0.6 + (best.ai_x and 0.25 or 0)) then
			F.plan = nil
			useMove(F, s, best, (cancel and 'COMBO ' or '') .. classOf(best):upper() .. ' ' .. best.st .. (why ~= '' and (' ' .. why) or ''), now)
			return macroBits(F)
		end
		F.decideAt = now + math.floor(2 + (1 - T.skill) * 6)
	end
	if not s.ctrl then return 0 end
	if now < (F.guardUntil or 0) then return F.guardBits end

	-- 4) movement: commit to a plan; re-plan when it runs out or no longer makes sense
	local _, _, dxw = relLocal(s, target)
	local behind = dxw < 0
	local dist = math.abs(dxw)
	local want = 25 + P.range * 230
	if not F.zoneOK then want = math.min(want, F.reachW + 30) end -- melee: hover just outside own reach
	if team.relay or team.rescue then want = 20 end
	local strike = math.max(15, F.reachW * 0.75)
	local band = 35 -- hysteresis: no bouncing on the edge of the preferred range
	local tag = why ~= '' and (' ' .. why) or ''
	-- the opponent's own reach (from its profile): inside it, a careful fighter holds back
	-- so the engine guards instantly - how people actually block
	local Fe = W.F[target.pn]
	local eReach = reachSeen(F, Fe, target) + 25

	-- how long it has been walking backwards (BREAK STALL above)
	if F.plan and F.plan.bits == BIT.B and F.plan.kind ~= 'block' and F.plan.kind ~= 'turn' then
		F.backSince = F.backSince or now
	elseif not (F.plan and F.plan.kind == 'block') then
		F.backSince = nil
	end
	local plan = F.plan
	if plan and (plan.untilT <= now or plan.target ~= target.pn or
		(plan.kind == 'approach' and dist <= want) or
		(plan.kind == 'stepin' and dist <= strike) or
		(plan.kind == 'retreat' and (dist >= want or s.backDist < 15 * s.scale)) or
		(plan.kind == 'hold' and math.abs(dist - want) > band * 2.5) or
		(plan.kind == 'block' and dist > eReach + band) or
		(plan.kind == 'turn' and not behind)) then
		plan = nil
	end
	if plan then
		F.intent = plan.intent
		return plan.bits
	end

	if behind then
		return setPlan(F, 'turn', BIT.B, 12, 'TURN' .. tag, now, target.pn)
	end
	if dist < eReach and target.mtype ~= 'H' and rnd() < P.guard * (0.35 + 0.65 * T.skill) then
		local low = perceive(target, rx(F, target), now).stype == 'C'
		return setPlan(F, 'block', low and bits('D', 'B') or BIT.B, 12 + rnd() * 20, 'BLOCK STANCE', now, target.pn)
	end
	-- backing off for too long (a wall, a teammate in the way, a range it can't reach): go in instead
	if (F.backSince or now) < now - 150 then
		F.backSince = nil
		startMacro(F, {{bits('U', 'F'), 3}}, 'BREAK STALL', now)
		return macroBits(F)
	end
	for _, a in ipairs(B.allies) do -- don't stack on a teammate (not with its back to the wall)
		if a.pn ~= s.pn and math.abs(a.x - s.x) < 18 and math.abs(target.x - a.x) < math.abs(target.x - s.x) and not team.relay
			and s.backDist > 40 * s.scale then
			return setPlan(F, 'space', BIT.B, 14, 'MAKE ROOM', now, target.pn)
		end
	end
	-- pincer: two allies on the same side of a lone enemy -> one goes over the top
	if #B.allies >= 2 and #B.enemies == 1 and F.persona ~= 'zoner' and dist < 80 then
		for _, a in ipairs(B.allies) do
			if a.pn ~= s.pn and (a.x < target.x) == (s.x < target.x) and math.abs(a.x - target.x) < dist and rnd() < 0.2 + P.jump then
				startMacro(F, {{bits('U', 'F'), 3}}, 'PINCER', now)
				return macroBits(F)
			end
		end
	end
	local nearest = 1e9
	for _, e in ipairs(B.enemies) do nearest = math.min(nearest, math.abs(e.x - s.x)) end
	if F.chargeMove and nearest > 150 and (s.powerReal or s.power) < s.powerMax * (#B.allies > 1 and 0.4 or 0.6)
		and now >= (F.chargeCD or 0) and target.mtype ~= 'A' and rnd() < 0.25 * F.chargeLove * (1.2 - P.agg) then
		local b = F.chargeMove['in'][1][1]
		startMacro(F, {{b, 30 + math.floor(rnd() * 40)}}, 'CHARGE KI', now)
		F.charging = true
		F.chargeCD = now + (#B.allies > 1 and 600 or 360) + math.floor(rnd() * 240)
		return macroBits(F)
	end
	if dist > want + band then
		if dist > 110 and rnd() < P.dash * 0.5 then
			startMacro(F, {{BIT.F, 2}, {0, 1}, {BIT.F, 10 + math.floor(rnd() * 10)}}, 'DASH IN' .. tag, now)
			return macroBits(F)
		end
		if dist < 170 and rnd() < P.jump * 0.25 then
			startMacro(F, {{bits('U', 'F'), 3}}, 'JUMP IN' .. tag, now)
			return macroBits(F)
		end
		return setPlan(F, 'approach', BIT.F, 20 + rnd() * 25, 'APPROACH' .. tag, now, target.pn)
	elseif dist < want - band then
		if s.backDist < 20 * s.scale then
			startMacro(F, {{bits('U', 'F'), 3}}, 'ESCAPE CORNER', now)
			return macroBits(F)
		end
		if rnd() < P.dash * 0.3 then
			startMacro(F, {{BIT.B, 2}, {0, 1}, {BIT.B, 2}}, 'BACKDASH', now)
			return macroBits(F)
		end
		return setPlan(F, 'retreat', BIT.B, 12 + rnd() * 14, 'CREATE SPACE', now, target.pn)
	end
	-- in range: hold ground; the patient ones hold longer; the proud ones taunt when ahead
	if P.taunt > 0 and rnd() < P.taunt * 0.15 and s.life > target.life and dist > 100 and now - (F.lastTaunt or -9999) > 600 then
		F.lastTaunt = now
		startMacro(F, {{BIT.s, 2}}, 'TAUNT', now)
		return macroBits(F)
	end
	if F.plan and F.plan.kind == 'hold' and rnd() < 0.25 + P.agg * 0.6 then
		return setPlan(F, 'stepin', BIT.F, 30, 'STEP IN' .. tag, now, target.pn) -- footsies: walk into own range
	end
	return setPlan(F, 'hold', 0, 15 + (1 - P.agg) * 45 * rnd(), 'READ', now, target.pn)
end


-- ------------------------------------------------------------------ overmind
-- The character's own AI keeps fighting. The brain adds what it lacks: reading the guard
-- height of incoming attacks and stepping straight into guard, team positioning (relay a
-- teammate's combo, rescue a teammate, focus a target), unison supers, and ki charging.
-- Identity moments: the author's AI fights the neutral and its combos; at the moments that define a
-- character the brain plays its signature move instead: the finisher on a nearly beaten enemy, the
-- comeback super when cornered at low life, the anti-air on a jump-in, the reversal out of block
-- pressure, the zoning tool at range, a rival super clash, a taunt over a downed enemy.
local function identityMoment(F, s, target, B, now)
	if now < (F.momAt or 0) then return false end
	local pe = perceive(target, rx(F, target), now)
	local d = math.abs(target.x - s.x)
	local skill = F.T.skill
	local function try(role, why, chance)
		local st = F.role[role]
		if not st or now < (F.mom[role] or 0) or rnd() > chance then return false end
		local m = F.moveBySt[st]
		if not m or (m.power or 0) > s.power or F.ban[st] then return false end
		if (s.stype == 'A') ~= (m.air == true) then return false end
		if role ~= 'taunt' and scoreMove(F, s, pe, m, false, {focus = target}) == nil then return false end
		useMove(F, s, m, why .. (F.sig[role] and F.sig[role] ~= '' and (' ' .. F.sig[role]) or ''), now)
		F.mom[role] = now + (MOMENT_CD[role] or 300)
		F.momAt = now + 20
		F.momN[why] = (F.momN[why] or 0) + 1
		return true
	end
	local fin = F.role.finisher and F.moveBySt[F.role.finisher]
	if fin and pe.life > 0 and pe.life <= (fin.dmg and fin.dmg > 0 and fin.dmg or 150) * 1.2
		and try('finisher', 'FINISHER', 0.45 + 0.4 * skill) then return true end
	if s.life < s.lifeMax * 0.3 and try('comeback', 'COMEBACK', 0.2 + 0.2 * F.P.super) then return true end
	if pe.stype == 'A' and pe.mtype ~= 'H' and d < 150
		and try('antiair', 'ANTI-AIR', math.min(0.95, 0.25 + 0.5 * skill + 0.4 * (F.A.air or 0))) then return true end
	if now - (F.blockedAt or -99) < 8 and d < 100 and try('reversal', 'REVERSAL', 0.2 + 0.4 * skill * F.P.punish) then return true end
	if F.mTag == 'RIVAL' and pe.power >= 1000 and d > 90 and d < 260
		and try('finisher', 'RIVAL CLASH', 0.02 * (1 + F.P.super)) then return true end
	if d > 200 and try('zoner', 'ZONE', 0.03 * (0.5 + F.P.proj)) then return true end
	if pe.stype == 'L' and d > 100 and try('taunt', 'TAUNT', 0.015 * (F.P.taunt * 6 + (F.traits.showoff and 1 or 0))) then return true end
	return false
end

local function overmind(F, s, S, B, now)
	local P, T = F.P, F.T
	F.now = now
	if s.mtype == 'H' and F.prevMtype ~= 'H' then
		F.hitBy = F.hitBy or {}
		local k = (F.prevMtype == 'A' and 'attacking' or (F.intent ~= '' and F.intent:match('^%S+') or 'own-ai'))
		F.hitBy[k] = (F.hitBy[k] or 0) + 1
	end
	F.prevMtype = s.mtype
	if s.st >= 150 and s.st <= 155 then F.blockedAt = now end
	confirmMove(F, s, now)
	if F.macro and F.charging then
		for _, e in ipairs(B.enemies) do
			if math.abs(e.x - s.x) < 100 or (e.mtype == 'A' and math.abs(e.x - s.x) < 160) then F.macro = nil end
		end
		if s.guardDist then F.macro = nil end
	end
	if not F.macro then F.charging = false end
	if F.macro then return macroBits(F) end
	if s.mtype == 'H' or s.stype == 'L' then F.intent = '' return 0 end

	-- a KO'd enemy lying nearer than every live one: the character's own AI keeps swinging at the body
	-- (user: Naruto Baryon beating Nagato's corpse while the Paths of Pain stood by). Walk past it.
	local live, ld = nil, 1e9
	for _, e in ipairs(B.enemies) do
		local d = math.abs(e.x - s.x)
		if d < ld then live, ld = e, d end
	end
	local body = false
	for _, e in pairs(S) do
		if live and e.side ~= s.side and not e.alive and not e.standby and math.abs(e.x - s.x) < math.min(ld, 160) then
			body = true
		end
	end
	if not body then F.bodySince = nil end
	-- only while the live one is out of reach (next to it, its AI must fight) and 2 s at most per body
	if body and ld > (F.reachW or 60) + 40 and now - (F.bodySince or now) < 120 then
		F.bodySince = F.bodySince or now
		F.holdAI = now + 3 -- its AI stays off while the body is the nearest thing to hit
		F.intent = 'IGNORE BODY'
		local _, _, dxw = relLocal(s, live)
		return dxw >= 0 and BIT.F or BIT.B
	end

	local target, why = chooseFocus(F, s, B, now)
	applyMatchup(F, target)
	teamEvent(F, why)
	if not target then return 0 end

	-- defense: only while its own AI isn't attacking
	if s.mtype ~= 'A' then
		local inc = nil
		local incD = 1e9
		for _, e in ipairs(B.enemies) do
			local pe = perceive(e, rx(F, e), now)
			local Fe = W.F[e.pn]
			local d = math.abs(e.x - s.x)
			-- an attack state older than 6 s is not an attack: Shadow V2's power charge is movetype A and its AI never leaves it at the wall,
			-- the foe guarded it forever (stalled rounds); now the foe punishes the charge
			if pe.mtype == 'A' and (e.time or 0) < 360 and d < reachSeen(F, Fe, e) + 60 and d < incD then inc, incD = pe, d end
		end
		-- a grab coming: the clever (and the ones a grab already got) tech out of it, once per threat
		if inc and inc.throw and s.ctrl and s.stype ~= 'A' and incD < 110 then
			if F.escRoll == nil then
				F.escRoll = rnd() < 0.20 + 0.55 * F.iq + 0.25 * (F.A.throw or 0) + 0.15 * knowOf(F, inc)
				if F.escRoll then
					startMacro(F, {{bits('U', 'B'), 3}}, 'ESCAPE THROW', now)
					return macroBits(F)
				end
			end
		else
			F.escRoll = nil
		end
		if ((s.guardDist and not staleThreat(B, s)) or inc) and (s.ctrl or (s.st >= 120 and s.st <= 155)) then
			F.guardT = F.guardT + 1
			if F.guardRoll == nil and F.guardT >= T.react * 0.5 then
				F.guardRoll = rnd() < math.min(0.97, (0.55 + 0.45 * T.skill) * (0.6 + 0.4 * P.guard) + 0.18 * (inc and knowOf(F, inc) or 0))
				if F.guardRoll then F.guards = F.guards + 1 end
			end
			if F.guardRoll then
				local g = inc and inc.gflag or 'HL'
				local low = g:find('L') and not g:find('H')
				if s.ctrl and (s.st < 120 or s.st > 155) then
					F.direct = s.stype == 'A' and 132 or (low and 131 or 130)
				end
				F.intent = low and 'GUARD LOW' or 'GUARD'
				return low and bits('D', 'B') or BIT.B
			end
		else
			F.guardT, F.guardRoll = 0, nil
		end
	end

	if not s.ctrl or s.mtype ~= 'I' then F.intent = 'OWN AI' return 0 end

	-- unison: a teammate's super is connecting -> pile ours onto the same target
	local unisonBond = 0
	for apn in pairs(B.allySuper) do unisonBond = math.max(unisonBond, (bond(F, W.F[apn]))) end
	if unisonBond >= 0.6 and rnd() < unisonBond then
		local best, bv = nil, 0
		for _, m in ipairs(F.moves) do
			if m.super and m.pure and m.power <= s.power then
				local v = scoreMove(F, s, perceive(target, rx(F, target), now), m, false, {focus = target, allySuper = true, bond = unisonBond})
				if v and v > bv then best, bv = m, v end
			end
		end
		if best then
			useMove(F, s, best, 'UNISON SUPER ' .. best.st, now)
			F.momN.UNISON = (F.momN.UNISON or 0) + 1
			return 0
		end
	end

	-- partner relay: a bonded teammate is comboing the target (Naruto + Minato, Tsunade + Tobirama): step in
	-- with a real move at once instead of waiting for the own AI to wander over
	if why:sub(1, 5) == 'RELAY' and now >= (F.mom.relay or 0) then
		local apn = tonumber(why:match('P(%d+)'))
		local pb = apn and bond(F, W.F[apn]) or 0
		if pb >= 0.6 and rnd() < 0.35 + 0.5 * pb then
			local tm = {focus = target, relay = target, pbond = pb, allyNear = true}
			local pe, best, bv = perceive(target, rx(F, target), now), nil, 0
			for _, m in ipairs(F.moves) do
				local v = scoreMove(F, s, pe, m, false, tm)
				if v and v > bv then best, bv = m, v end
			end
			if best then
				F.mom.relay = now + 90
				useMove(F, s, best, 'PARTNER RELAY ' .. best.st, now)
				F.momN.TEAMUP = (F.momN.TEAMUP or 0) + 1
				return 0
			end
		end
	end

	if identityMoment(F, s, target, B, now) then return 0 end

	-- outside close range the brain steers; inside it, the character's own AI fights
	local nearest = 1e9
	for _, e in ipairs(B.enemies) do nearest = math.min(nearest, math.abs(e.x - s.x)) end
	if nearest < 130 then F.intent = 'OWN AI' return 0 end
	if why ~= '' then
		F.intent = why
		return (target.x - s.x) * s.facing >= 0 and BIT.F or BIT.B
	end
	-- ki charge: a short burst when low on power, then a cooldown (4v4 test: uncapped charging kept
	-- two fighters powering up at the back for most of the round)
	local team = #B.allies > 1
	if F.chargeMove and (s.powerReal or s.power) < s.powerMax * (team and 0.4 or 0.6) and now >= (F.chargeCD or 0)
		and target.mtype ~= 'A' and nearest > 220 and rnd() < 0.02 * F.chargeLove then
		startMacro(F, {{F.chargeMove['in'][1][1], 30 + math.floor(rnd() * 40)}}, 'CHARGE KI', now)
		F.charging = true
		F.chargeCD = now + (team and 600 or 360) + math.floor(rnd() * 240)
		return macroBits(F)
	end
	-- idle at range: in team fights the own AI often watches one far enemy and never closes in.
	-- Zoners hold projectile range; everyone else walks in, dashing now and then by aggression.
	local hold = F.hold or (F.persona == 'zoner' and 300 or 130) -- its own preferred range (identity)
	F.stallT = (nearest > hold and s.mtype == 'I') and (F.stallT or 0) + 1 or 0
	if F.stallT > 40 then
		local toward = (target.x - s.x) * s.facing >= 0
		if toward and F.stallT % 75 == 41 and rnd() < 0.35 + P.agg * 0.5 then
			startMacro(F, {{BIT.F, 2}, {0, 1}, {BIT.F, 8 + math.floor(rnd() * 10)}}, 'DASH IN', now)
			return macroBits(F)
		end
		F.intent = 'ENGAGE'
		return toward and BIT.F or BIT.B
	end
	F.intent = 'OWN AI'
	return 0
end

-- ------------------------------------------------------------------ overlay
local font, lines = nil, {}
local function overlay(S)
	local show = W.overlay == 'on' or W.overlay == 'auto' and
		(roundState() <= 1 or W.fightStart and gameTime() - W.fightStart < 240)
	if not show then return end
	if not font then
		font = fontNew('f-6x9.def')
	end
	local y = 700 -- bottom-left, inside a 4:3 pillarbox too; stacks upward
	for pn = 1, 8 do
		local F, s = W.F[pn], S[pn]
		if F and s then
			local ts = lines[pn]
			if not ts then
				ts = textImgNew()
				textImgSetFont(ts, font)
				textImgSetLocalcoord(ts, 1280, 720)
				textImgSetScale(ts, 1.4, 1.4)
				lines[pn] = ts
			end
			local col = s.side == 1 and {120, 200, 255} or {255, 150, 120}
			textImgSetColor(ts, col[1], col[2], col[3], 255)
			textImgSetPos(ts, 170, y)
			local tag = ''
			for apn, A in pairs(W.F) do
				if apn ~= pn and S[apn] and S[apn].side == s.side then
					local b, why = bond(F, A)
					if why ~= '' then tag = '  [' .. why .. ' w/ P' .. apn .. ']' end
				end
			end
			textImgSetText(ts, string.format('P%d %s  T%d %s%s  %s  %s', pn, s.name or '?', F.tier, F.persona, tag,
				(F.mode == 'pilot' and 'BRAIN') or (F.mode == 'overmind' and 'OVERMIND') or (F.mode == 'coach' and 'OWN AI' or 'HUMAN'),
				(F.mode == 'pilot' or F.mode == 'overmind') and W.enabled and F.intent or ''))
			textImgDraw(ts)
			y = y - 16
		end
	end
end

-- ------------------------------------------------------------------ presence: call-outs and banter
-- One caption per side under the lifebars: the name of a super as it starts, and a line between fighters
-- who know each other when the fight begins (rivals, partners on opposite sides, the same faction).
W.cap, W.capTs, W.captions = {}, {}, getCommandLineValue('-woo.captions') ~= 'off'

local function say(s, text, ticks)
	if not W.captions or not text or text == '' then return end
	if #text > 56 then text = text:sub(1, 53) .. '...' end -- half the 4:3 width at this scale
	W.cap[s.side] = {text = text, untilT = gameTime() + (ticks or 150), col = s.side == 1 and {120, 200, 255} or {255, 150, 120}}
end

local function drawCaptions()
	if not W.captions then return end
	local now = gameTime()
	for side = 1, 2 do
		local c = W.cap[side]
		if c and now < c.untilT then
			font = font or fontNew('f-6x9.def')
			local ts = W.capTs[side]
			if not ts then
				ts = textImgNew()
				textImgSetFont(ts, font)
				textImgSetLocalcoord(ts, 1280, 720)
				textImgSetScale(ts, 1.4, 1.4)
				textImgSetAlign(ts, side == 1 and 1 or -1) -- side 1 grows rightwards from the left edge, side 2 leftwards
				W.capTs[side] = ts
			end
			textImgSetColor(ts, c.col[1], c.col[2], c.col[3], math.floor(255 * clamp((c.untilT - now) / 30, 0, 1)))
			textImgSetPos(ts, side == 1 and 172 or 1108, 150) -- inside a 4:3 pillarbox (x 160..1120 of 1280)
			textImgSetText(ts, c.text)
			textImgDraw(ts)
		end
	end
end

-- What a fighter is called in a line: the lore key's words that appear in its display name, else the display name.
local function shortName(Fx, s)
	local disp = s.name or '?'
	if not (Fx and Fx.lore) then return disp end
	local out = {}
	for w in Fx.lore.key:gmatch('[^_]+') do
		local a, b = disp:lower():find(w, 1, true)
		if a then out[#out + 1] = disp:sub(a, a):upper() .. disp:sub(a + 1, b) end
	end
	return #out > 0 and table.concat(out, ' ') or disp
end

-- A line for the moment two fighters meet, from what they are to each other. Nothing for strangers.
local function banterLine(F, s, e)
	local Fe = W.F[e.pn]
	if not (F.lore and Fe and Fe.lore) then return nil end
	local k, nm = knowOf(F, e), shortName(Fe, e)
	if F.lore.key == Fe.lore.key then return 'Same face. Same moves. Prove which of us is real.' end
	if listHas(F.lore.rivals, Fe.lore.key) or listHas(Fe.lore.rivals, F.lore.key) then
		if F.traits.proud then return nm .. '. I will not lose to you again.' end
		if F.traits.ruthless then return nm .. '. This ends here.' end
		if F.traits.loyal then return nm .. ', I will stop you today.' end
		return nm .. '. Let us settle this.'
	end
	if listHas(F.lore.partners, Fe.lore.key) or listHas(Fe.lore.partners, F.lore.key) then
		return nm .. '... I know every move you have. Do not hold back.'
	end
	if k >= 0.5 then return 'I know how you fight, ' .. nm .. '.' end
	return nil
end

-- At the first bell: each side's strongest tie speaks, once per pair per match.
local function openingBanter(S, now)
	W.spoke = W.spoke or {}
	for pn, F in pairs(W.F) do
		local s = S[pn]
		if s and s.alive and not s.standby and (F.mode == 'overmind' or F.mode == 'pilot') then
			for _, e in pairs(S) do
				local key = pn .. '>' .. ekey(e)
				if e.side ~= s.side and e.alive and not e.standby and not W.spoke[key] and not (W.cap[s.side] and now < W.cap[s.side].untilT) then
					local k = knowOf(F, e)
					if k >= 0.5 and rnd() < (k >= 0.9 and 0.95 or 0.5) then
						local line = banterLine(F, s, e)
						if line then
							W.spoke[key] = true
							say(s, shortName(F, s) .. ': ' .. line, 240)
						end
					end
				end
			end
		end
	end
end

-- A super starting: log it with the situation (the proof of the finisher discipline) and call its name out.
local function superWatch(F, s, B, now)
	if not F.superSt[s.st] then F.lastSuperSt = nil return end -- (some supers start in a movetype-I wind-up state)
	if F.lastSuperSt == s.st then return end
	F.lastSuperSt = s.st
	local lowest = 2
	for _, e in ipairs(B.enemies) do lowest = math.min(lowest, e.life / math.max(1, e.lifeMax)) end
	local m = F.moveBySt[s.st]
	F.supersFired = (F.supersFired or 0) + 1
	local cost = m and m.power or 0
	local need = gateOpenAt(F) * SUPER_LEVEL[clamp(math.floor(cost / 1000), 1, #SUPER_LEVEL)] -- enemy life share that opens this one
	local atk = m and ((m.hits or 0) > 0 or m.proj or m.helper or m.throw or (m.dmg or 0) > 0) and 1 or 0 -- buffs/summons are not gated
	-- it started with the gate shut (the clamp did not cover this move's real threshold): cancel it, refund the
	-- power, and learn a lower ceiling for this cost so it cannot start again
	if atk == 1 and cost >= 1000 and s.time <= 30 and F.powBefore and F.closedBefore and not F.free[s.st]
		and F.closedBefore[cost] and F.closedAfter[cost] then
		-- vetoes of this state in the last 3 s: a cancel loop (Daz Bones' Atomic Spurt retries every tick) is a move that
		-- needs no meter, two vetoes far apart are a real super still being calibrated (VegettoSSB's Sudden Storm, Jiren's
		-- Energy Wave Volley were freed by the old "2 vetoes in a match" rule and then fired at 90% enemy life)
		local recent = 0
		local vt = F.vetoN[s.st] or {}
		vt[#vt + 1] = now
		F.vetoN[s.st] = vt
		for _, t in ipairs(vt) do if now >= t and now - t <= 180 then recent = recent + 1 end end -- (the clock restarts in Turns)
		-- Only a cancel loop frees a state. A super that merely needs no meter and comes now and then (Pain's Shinra
		-- Tensei, ~once per 4 s) stays vetoed while the gate is shut. A 1-bar one cancelled 3 times in 3 s is a
		-- spammed kit move (Daz Bones' Atomic Spurt): freed for good.
		if cost < 2000 and recent >= 3 then
			F.free[s.st] = true
			local f = io.open(LEAK_PATH, 'a')
			if f then f:write(F.def .. '\tfree\t' .. s.st .. '\n') f:close() end
			log(string.format('super FREE P%d %s st=%d "%s" cost=%d had=%d (cancel loop: not gated)', s.pn, F.def or '?',
				s.st, m and m.name or '', cost, F.powBefore))
			return
		end
		-- A 2+ bar finisher its AI retries every few ticks (KillerB's ultimate, Black Frieza's Nova Strike: 339 cancels
		-- in 10 matches) only stutters when cancelled, and freeing it let it fire at full enemy life: from the 2nd cancel
		-- in 3 s the brain fights for that fighter (pilot, own AI off) until this level's gate opens (step()).
		if cost >= 2000 and recent >= 2 and F.takeover ~= cost then
			F.takeover = cost
			log(string.format('super HOLD P%d %s st=%d "%s" cost=%d (AI retries it: brain pilots until the gate opens)',
				s.pn, F.def or '?', s.st, m and m.name or '', cost))
		end
		local old = F.leak[cost]
		if F.powBefore < cost then -- started below its cost: its real threshold is lower, learn it (a start above the
			-- cost came from a released clamp, e.g. right after a ki-charge state: nothing to learn, just cancel)
			F.leak[cost] = math.max(100, math.min(old or 1e9, math.floor(F.powBefore * 0.5)))
			if F.leak[cost] ~= old then -- at the floor nothing more to learn: do not grow the file on every veto
				local f = io.open(LEAK_PATH, 'a')
				if f then f:write(F.def .. '\t' .. cost .. '\t' .. F.leak[cost] .. '\n') f:close() end
			end
		end
		changeState(s.stype == 'A' and 50 or (s.stype == 'C' and 11 or 0)) -- idle, crouch, or the common air state
		-- control back, as when a move ends (ChangeState ctrl = 1): the super's ctrl = 0 carried over and left the fighter
		-- frozen in idle until hit; two vetoed at once froze the round (stall_ab: platinum sperm vs Titan Colossal).
		-- Lua has no setCtrl: data/woo_brain.zss does ctrlSet on the next tick.
		mapSet('_woo_ctrl', 1)
		local hold = math.min(F.powBefore, F.leak[cost] or (cost - 1))
		F.real, F.clampSet = F.powBefore, hold -- what it earned stays banked (paid back when the gate opens)
		setPower(hold)
		s.power = hold
		F.vetoed = (F.vetoed or 0) + 1
		F.lastSuperSt = nil
		log(string.format('super VETO P%d %s st=%d "%s" cost=%d had=%d foe_low=%d%% need=%d%% -> ceiling %d', s.pn, F.def or '?', s.st,
			m and m.name or '', cost, F.powBefore, math.floor(lowest * 100), math.floor(need * 100), F.leak[cost] or (cost - 1)))
		return
	end
	log(string.format('super P%d %s st=%d "%s" cost=%d atk=%d foe_low=%d%% need=%d%% self=%d%% allies=%d round=%d t=%d pw=%d stime=%d shut=%s/%s before=%s', s.pn, F.def or '?', s.st,
		m and m.name or '', cost, atk, math.floor(lowest * 100), math.floor(need * 100), math.floor(100 * s.life / math.max(1, s.lifeMax)),
		#B.allies, W.round or 0, now, s.power, s.time, tostring(F.closedBefore and F.closedBefore[cost]), tostring(F.closedAfter and F.closedAfter[cost]),
		tostring(F.powBefore)))
	if m and m.name and #m.name > 2 then say(s, m.name:upper(), 120) end
end

-- ------------------------------------------------------------------ round log
local function summary(S)
	if (W.profN or 0) > 0 then
		log(string.format('perf brain avg=%.3f ms max=%.2f ms, in the fight %.2f ms (tick %d), over %d ticks', 1000 * W.profT / W.profN,
			1000 * W.profMax, 1000 * (W.profMaxF or 0), W.profMaxAt or 0, W.profN))
		W.profT, W.profN, W.profMax, W.profMaxF = 0, 0, 0, 0
	end
	for pn, F in pairs(W.F) do
		local banned = 0
		for _ in pairs(F.ban) do banned = banned + 1 end
		local s = S[pn]
		local lost, result = false, 'unknown'
		if s and player(pn) then
			lost = lose()
			-- Native flags handle time-over, draws and KO members of a winning team.
			result = win() and 'win' or lost and 'loss' or drawGame() and 'draw' or 'unknown'
		end
		if lost then -- how badly: the best enemy life share left (second wind grows with it)
			F.lastDom = 0
			for pn2, s2 in pairs(S) do
				if s2.side ~= s.side then F.lastDom = math.max(F.lastDom, s2.life / math.max(1, s2.lifeMax)) end
			end
		end
		local learned = (F.mode == 'overmind' or F.mode == 'pilot') and learnRound(F, lost) or ''
		log(string.format('round %d end P%d %s mode=%s life=%d tries=%d came_out=%d landed=%d guards=%d banned=%d result=%s',
			W.round, pn, F.def or '?', F.mode, s and s.life or -1, F.tries, F.cameOut or 0, F.landed, F.guards, banned, result))
		log(string.format('activity P%d act=%d moved=%d life=%d maxcombo=%d loopbreaks=%d whiffs=%d supers=%d', pn, F.actTicks or 0,
			math.floor(F.moved or 0), s and s.life or -1, s and s.maxHits or 0, s and s.breaks or 0, F.whiffs or 0, F.supersFired or 0))
		local label = ({win = 'won', loss = 'lost', draw = 'drew'})[result] or 'unknown'
		if learned ~= '' then log(string.format('adapt P%d (%s round, lost so far %d, iq %.2f): %s', pn, label, F.roundsLost, F.iq, learned)) end
		F.whiffs, F.supersFired = 0, 0
		local mp = {}
		for k, v in pairs(F.momN or {}) do table.insert(mp, k .. '=' .. v) end
		table.sort(mp)
		log(string.format('identity P%d matchup=%s moments: %s', pn, (F.mTag and F.mTag ~= '') and F.mTag or '-', table.concat(mp, ' ')))
		F.momN = {}
		F.actTicks, F.moved = 0, 0
		if W.verbose then
			local parts = {}
			for stno, st in pairs(F.stats) do
				table.insert(parts, string.format('%d:%d/%d%s', stno, st.ok, st.tries, F.ban[stno] and 'X' or ''))
			end
			table.sort(parts)
			log('   moves ok/tries: ' .. table.concat(parts, ' '))
			local hb = {}
			for k, v in pairs(F.hitBy or {}) do table.insert(hb, k .. '=' .. v) end
			table.sort(hb)
			log('   got hit while: ' .. table.concat(hb, ' '))
			local lb = {}
			for k, v in pairs(F.lostBy or {}) do table.insert(lb, k .. '=' .. v) end
			table.sort(lb)
			log('   life lost by: ' .. table.concat(lb, ' '))
			F.hitBy, F.lostBy = {}, {}
		end
	end
end

-- ------------------------------------------------------------------ main loop
-- Intro watchdog. Some intros wait for a condition that never comes and assert Intro every tick, so
-- the round never starts: Gogeta (SSJ) only leaves state 190 when teammode != simul (MUGEN stalls the
-- same way). Stand-anim intro states after 4 s, or any non-idle state after 15 s, go to state 0.
local INTRO_IDLE, INTRO_MAX = 240, 900
local function introWatchdog(S, rs)
	if rs ~= 1 then W.introT = 0 return end
	W.introT = (W.introT or 0) + 1
	if W.introT < INTRO_IDLE or W.introT % 30 ~= 0 then return end
	for pn, s in pairs(S) do
		local waiting = s.st >= 190 and s.st <= 199 and s.anim == 0
		if s.st ~= 0 and (waiting or W.introT >= INTRO_MAX) then
			log(string.format('intro watchdog: P%d %s stuck in state %d anim %d for %d ticks -> state 0',
				pn, W.F[pn] and W.F[pn].def or '?', s.st, s.anim, W.introT))
			player(pn)
			changeState(0)
		end
	end
end

local function step()
	if not player(1) then return end
	local now = gameTime()
	-- gameTime() starts over when a Turns member loads: every "until tick N" from before would hold for
	-- thousands of ticks (user log: Jackie kept guarding = walking back the whole round)
	if W.lastNow and now < W.lastNow then
		for _, F in pairs(W.F) do
			F.guardUntil, F.decideAt, F.focusUntil, F.momAt, F.lastProj, F.lastTaunt = nil, 0, 0, nil, nil, nil
			F.lastIntentAt, F.chargeCD, F.holdAI, F.backSince, F.blockedAt, F.bodySince = nil, nil, nil, nil, nil, nil
			F.mom, F.plan, F.macro, F.pend, F.outcome, F.stateEnter = {}, nil, nil, nil, nil, now
			F.gateUntil, F.partnerUntil, F.eAtk = {}, nil, {}
		end
		W.hist, W.fightStart, W.cap = {}, nil, {}
	end
	W.lastNow = now
	if now == W.lastTick then return end
	W.lastTick = now
	local rs = roundState()

	local S = {}
	for pn = 1, 8 do S[pn] = snap(pn) end
	for pn, s in pairs(S) do
		W.hist[pn] = W.hist[pn] or {}
		W.hist[pn][now % 64] = {t = now, mtype = s.mtype, stype = s.stype, ctrl = s.ctrl, st = s.st, gflag = s.gflag, throw = s.throw}
	end
	if rs == 2 then W.fightStart = W.fightStart or now else W.fightStart = nil end
	introWatchdog(S, rs)

	-- new match or new characters: rebuild brains
	local mn = matchNo()
	if mn ~= W.matchNo then W.matchNo, W.aiSlot = mn, {} end
	for pn, s in pairs(S) do
		local F = W.F[pn]
		if not F or F.id ~= s.id then
			W.F[pn] = initFighter(s)
			W.F[pn].applied = -1
			if W.F[pn].eligible then W.aiSlot[pn] = true end
			-- a human (or brain-off) fighter in a slot a pilot had remapped gets its controller back
			if W.F[pn].mode ~= 'pilot' and W.remap0[pn] then
				player(pn)
				if getRemapInput(pn) == DEAD_INPUT_SLOT then remapInput(pn, W.remap0[pn]) end
			end
		end
	end
	for pn in pairs(W.F) do
		if not S[pn] then W.F[pn] = nil end
	end

	local rn = roundNo()
	if rn ~= W.round then
		W.round, W.logged = rn, false
	end
	-- Round flags can still change during hit resolution (state 3); state 4 is final.
	if rs >= 4 and not W.logged then
		W.logged = true
		summary(S)
	end
	for pn, F in pairs(W.F) do
		if F.applied ~= rn and rs <= 2 then
			local newRound = F.scaledRound ~= rn
			local gap = 0 -- tiers between it and the strongest enemy: the underdog's second wind
			for pn2, F2 in pairs(W.F) do
				if S[pn2] and S[pn2].side ~= S[pn].side then gap = math.max(gap, (F2.tier or 3) - F.tier) end
			end
			applyRound(F, S[pn], newRound, gap)
			F.applied, F.scaledRound = rn, rn
			if newRound then
				F.ban, F.macro, F.pend, F.plan = {}, nil, nil, nil -- stats carry over: learning lasts the match
				F.tries, F.landed, F.guards, F.cameOut = 0, 0, 0, 0
				local costs = {}
				for _, l in ipairs(F.supers) do costs[#costs + 1] = l.cost end
				log(string.format('round %d start P%d %s mode=%s tier=%d persona=%s moves=%d own_ai=%s eligible=%s profile=%s iq=%.2f supers=%s wind=%.2f',
					rn, pn, F.def or '?', F.mode, F.tier, F.persona, #F.moves,
					F.prof and F.prof.ai.quality or '?', tostring(F.eligible), tostring(F.prof ~= nil),
					F.iq, #costs > 0 and table.concat(costs, '/') or '-', F.secondWind or 0))
			end
		end
	end
	-- stall breaker (watch matches run with infinite time): a round that stopped progressing ends as a
	-- time over, decided on life. Caminhos de Pain hides its main body invulnerable until its Paths
	-- (helpers) are destroyed; a fighter whose AI only aims at players never wins that, and never loses.
	local allAI = true
	for _, F in pairs(W.F) do if not F.eligible then allAI = false end end
	if rs == 2 and W.fightStart and allAI then
		local tot = 0
		for _, s in pairs(S) do if s.alive then tot = tot + s.life end end
		if not W.prog or tot < W.prog.life * 0.90 or now < W.prog.t then W.prog = {t = now, life = tot} end
		local age = now - W.fightStart
		if W.stalled ~= W.round and ((age > 7200 and now - W.prog.t > 3600) or age > 21600) then -- 2 min and <10% life lost in 60 s, or 6 min (MUGEN's own round is 99 s)
			W.stalled = W.round
			log(string.format('round %d STALL after %d s: forced time over', W.round, math.floor(age / 60)))
			setTime(0)
			W.prog = nil
		end
	else
		W.prog = nil
	end
	if W.shot and rs == 2 and now % W.shot == 0 then screenshot() end
	if W.shot1 and rs == 2 and W.fightStart and now == W.fightStart + (W.shot1 == 1 and 120 or W.shot1) then screenshot() end
	if W.endafter and not W.fightStart and now > 0 and now % 300 == 0 then -- crash scan: who holds the round?
		local t = {}
		for pn, s in pairs(S) do
			player(pn)
			local ok, an = pcall(anim)
			t[#t + 1] = string.format('P%d st=%d anim=%s time=%d ctrl=%s', pn, s.st, ok and tostring(an) or '?', s.time, tostring(s.ctrl))
		end
		table.sort(t)
		log(string.format('waiting rs=%d t=%d %s', rs, now, table.concat(t, ' | ')))
	end
	if W.endafter and W.fightStart and now >= W.fightStart + W.endafter and not W.ended then
		W.ended = true
		for pn, F in pairs(W.F) do log(string.format('loadcheck ok P%d %s', pn, F.def or '?')) end
		endMatch()
	end
	if rs == 2 then
		for pn, s in pairs(S) do
			local F = W.F[pn]
			if F then
				if s.mtype == 'A' then F.actTicks = (F.actTicks or 0) + 1 end
				if F.lastX then F.moved = (F.moved or 0) + math.abs(s.x - F.lastX) end
				F.lastX = s.x
			end
		end
	end

	-- -woo.guardtest: P1 just holds back all round (does engine guarding work through AssertInput?)
	if W.guardtest and rs == 2 and S[1] then
		W.gt = W.gt or {hit = 0, guard = 0, prev = ''}
		local s1 = S[1]
		if s1.mtype == 'H' and W.gt.prev ~= 'H' then W.gt.hit = W.gt.hit + 1 end
		if s1.st >= 150 and s1.st <= 155 and (W.gt.prevSt or 0) ~= s1.st then W.gt.guard = W.gt.guard + 1 end
		W.gt.prev, W.gt.prevSt = s1.mtype, s1.st
		if now % 300 == 0 then log(string.format('guardtest hits=%d guardstun_entries=%d st=%d facing=%d', W.gt.hit, W.gt.guard, s1.st, s1.facing)) end
		player(1); mapSet('_woo_on', 1); mapSet('_woo_keys', BIT.B)
		return
	end
	-- -woo.probe: P1 presses one input at a time from neutral and logs the state it lands in
	if W.probe and rs == 2 and S[1] and W.F[1] then
		local seqs = {{'a', {{16, 2}}}, {'b', {{32, 2}}}, {'c', {{64, 2}}}, {'x', {{128, 2}}},
			{'y', {{256, 2}}}, {'z', {{512, 2}}}, {'D+a', {{2, 3}, {18, 2}}}, {'QCF+a', {{2, 2}, {10, 2}, {8, 2}, {16, 2}}}}
		local P = W.probeState or {i = 0, t = 0}
		W.probeState = P
		P.t = P.t + 1
		local item = seqs[P.i + 1]
		local keys = 0
		if item then
			local at = 0
			for _, st in ipairs(item[2]) do
				if P.t > 30 + at and P.t <= 30 + at + st[2] then keys = st[1] end
				at = at + st[2]
			end
			if P.t == 30 + at + 1 or P.t == 30 + at + 4 or P.t == 30 + at + 10 then
				log(string.format('probe %s +%d: st=%d mtype=%s', item[1], P.t - 30 - at, S[1].st, S[1].mtype))
			end
			if P.t >= 150 then P.i, P.t = P.i + 1, 0 end
		end
		player(1)
		mapSet('_woo_on', 1)
		mapSet('_woo_keys', keys)
		for pn = 2, 8 do if player(pn) then mapSet('_woo_keys', 0) end end
		return
	end

	if rs == 2 and W.fightStart and now == W.fightStart + 30 and W.enabled then openingBanter(S, now) end

	local boards = {}
	for pn, F in pairs(W.F) do
		local s = S[pn]
		local keys = 0
		local live = (F.mode == 'overmind' or F.mode == 'pilot') and W.enabled and rs == 2 and s.alive and not s.standby
		if live then
			-- scouting + hit ledger, finisher discipline (power gate), fighting spirit, super call-outs
			boards[s.side] = boards[s.side] or teamBoard(S, s.side)
			player(pn)
			observe(F, s, boards[s.side])
			gatePower(F, s, boards[s.side], now)
			clutch(F, s, S)
			superWatch(F, s, boards[s.side], now)
		elseif (F.mode == 'overmind' or F.mode == 'pilot') and W.enabled and rs == 1 and s.alive and not s.standby then
			-- intro: shut the gate before the first fight tick (a fighter starting with power fired on tick 1)
			boards[s.side] = boards[s.side] or teamBoard(S, s.side)
			player(pn)
			gatePower(F, s, boards[s.side], now, true)
		elseif F.clampSet ~= nil then
			player(pn)
			releasePower(F, s)
		end
		if F.mode == 'overmind' and W.enabled and rs == 2 and s.alive and not s.standby then
			boards[s.side] = boards[s.side] or teamBoard(S, s.side)
			if F.takeover and (F.gateUntil[F.takeover] or 0) < now then
				F.holdAI = now + 2 -- its AI loops a finisher the gate holds back (superWatch): the pilot fights instead
				keys = decide(F, s, S, boards[s.side], now)
			else
				if F.takeover then log(string.format('super HOLD end P%d %s cost=%d (gate open)', s.pn, F.def or '?', F.takeover)) end
				F.takeover = nil
				keys = overmind(F, s, S, boards[s.side], now)
			end
		elseif F.mode == 'pilot' and W.enabled and rs == 2 and s.alive and not s.standby then
			boards[s.side] = boards[s.side] or teamBoard(S, s.side)
			keys = decide(F, s, S, boards[s.side], now)
		end
		if W.trace and now % W.trace == 0 and rs == 2 then
			local e = S[s.pn % 2 + 1] or S[(s.pn == 1) and 2 or 1]
			log(string.format('T%d P%d st=%d %s%s ctrl=%s pw=%d life=%d | %s keys=%d dir=%s | foe st=%d %s%s ctrl=%s life=%d dx=%d hits=%d',
				now, s.pn, s.st, s.stype, s.mtype, tostring(s.ctrl), s.power, s.life, F.intent, keys, tostring(F.direct),
				e and e.st or -1, e and e.stype or '?', e and e.mtype or '?', tostring(e and e.ctrl), e and e.life or -1,
				e and math.floor(math.abs(e.x - s.x)) or -1, e and e.rh or 0))
		end
		player(pn)
		if F.mode == 'overmind' then
			local want = (F.burst and F.macro ~= nil or (F.holdAI or 0) > now) and rs == 2
			if want and not F.bursting then
				local cur = getRemapInput(pn)
				if cur ~= DEAD_INPUT_SLOT then W.remap0[pn] = cur end
				setAILevel(0) -- its AI pauses and `!ailevel` command routes open, like a pilot
				remapInput(pn, DEAD_INPUT_SLOT)
				F.bursting = true
			elseif not want and F.bursting then
				setAILevel(F.T.ai)
				remapInput(pn, W.remap0[pn] or pn)
				F.bursting, F.burst = false, false
			end
		end
		mapSet('_woo_keys', keys)
		if F.direct then
			changeState(F.direct)
			F.direct = nil
		end
	end
	overlay(S)
	drawCaptions()
end

-- The pause menu reads every player's controller (menu.lua getInput(-1, ...)); a pilot's controller points
-- at the dead slot, so hand the real mappings back while paused and re-apply on the next fight tick.
local function pauseRelease()
	for pn, v in pairs(W.remap0) do
		if getRemapInput(pn) == DEAD_INPUT_SLOT then remapInput(pn, v) end
		if W.F[pn] then W.F[pn].applied = -1 end
	end
end

hook.add('loop', 'woo_brain', function()
	if paused() then pauseRelease() return end
	if W.err then return end
	local t0 = os.clock()
	local ok, e = pcall(step)
	local dt = os.clock() - t0 -- brain cost per tick, logged per round (perf line)
	if W.fightStart and dt > (W.profMaxF or 0) then W.profMaxF, W.profMaxAt = dt, gameTime() - W.fightStart end -- fight ticks only
	W.profT, W.profN, W.profMax = (W.profT or 0) + dt, (W.profN or 0) + 1, math.max(W.profMax or 0, dt)
	if not ok then
		W.err = tostring(e)
		log('ERROR (brain off; fighters handed back to their own AI): ' .. W.err)
		for pn = 1, 8 do
			if player(pn) then
				mapSet('_woo_on', 0); mapSet('_woo_keys', 0)
				local F = W.F[pn]
				if F and F.mode == 'pilot' and F.eligible then setAILevel(F.T.ai) end
				if F and F.bursting then
					setAILevel(F.T.ai)
					remapInput(pn, W.remap0[pn] or pn)
					F.bursting = false
				end
			end
		end
	end
end)

-- F7 overlay, F8 brain on/off (live A/B against the stock AI).
addHotkey('F7', false, false, false, true, false,
	"woo_brain.overlay = ({auto = 'on', on = 'off', off = 'auto'})[woo_brain.overlay] or 'auto'")
addHotkey('F8', false, false, false, true, false, [[
	woo_brain.enabled = not woo_brain.enabled
	for pn, F in pairs(woo_brain.F) do F.applied = -1 end
]])

math.randomseed(os.time())
log('woo_brain ' .. VERSION .. ' (BETA) loaded (enabled=' .. tostring(W.enabled) .. ')')
