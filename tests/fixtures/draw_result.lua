-- Force a double KO to check native draw handling and the engine's configured draw limit.
-- Loaded only in a test sandbox; the player configuration stays untouched.
local seen = {}
hook.add('loop', 'woo_draw_result_regression', function()
	local rn = roundNo()
	if roundState() ~= 2 or seen[rn] then return end
	seen[rn] = true
	for pn = 1, 8 do
		if player(pn) and not standby() then setLife(0) end
	end
end)
