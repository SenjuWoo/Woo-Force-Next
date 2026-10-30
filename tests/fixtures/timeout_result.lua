-- Real-engine regression: a living underdog loses on time and receives second wind next round.
-- Loaded only through run_match.py's sandbox --module option; no player settings are changed.
local seen = {}
hook.add('loop', 'woo_timeout_result_regression', function()
	local rn = roundNo()
	if roundState() ~= 2 or seen[rn] then return end
	seen[rn] = true
	if rn == 2 then
		local F = assert(woo_brain.F[1])
		assert(F.roundsLost == 1, 'time-over loss must be counted exactly once')
		assert(F.secondWind > 0, 'time-over underdog must receive second wind')
		print(string.format('TIMEOUT REGRESSION PASS losses=%d wind=%.3f', F.roundsLost, F.secondWind))
	end
	assert(player(1))
	setLife(math.max(1, math.floor(lifeMax() * 0.1)))
	assert(player(2))
	setLife(math.max(1, math.floor(lifeMax() * 0.9)))
	setTime(0)
end)
