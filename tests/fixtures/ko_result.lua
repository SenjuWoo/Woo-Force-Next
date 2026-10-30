-- Force active side-one fighters to lose by KO, including successive Turns members.
-- Used only by the sandbox match runner to check native result and completion handling.
local seen = {}
hook.add('loop', 'woo_ko_result_regression', function()
	local rn = roundNo()
	if roundState() ~= 2 or seen[rn] then return end
	seen[rn] = true
	for pn = 1, 8 do
		if player(pn) and teamSide() == 1 and not standby() then setLife(0) end
	end
end)
