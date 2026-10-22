-- Select-screen screenshot harness for woo/tools/select_shot.py (test sandboxes only: loaded through the
-- sandbox config's [Common] Modules, never from external/mods). Opens Watch mode's select screen, shoots
-- the grid, then puts the P1 cursor on each requested cell and shoots its big portrait, then exits.
local cells = {}
for x, y in (getCommandLineValue('-woo.cells') or ''):gmatch('(%d+),(%d+)') do
	table.insert(cells, {tonumber(x), tonumber(y)})
end
local started, n, k = false, 0, 0

hook.add('main.menu.loop', 'woo_selshot', function()
	if started then return end
	started = true
	main.f_default() -- menu state (cpuSide, teamMenu...) the item functions expect
	local glic = getLastInputController -- no key pressed yet in a scripted run: use controller 1
	getLastInputController = function() local c = glic() return (c ~= nil and c >= 1) and c or 1 end
	local f = main.t_itemname.watch({}, 1)
	if not getCommandLineValue('-woo.teammenu') then
		for side = 1, 2 do -- single only: the team menu auto-skips and the cursor goes live
			main.teamMenu[side].simul, main.teamMenu[side].tag, main.teamMenu[side].turns = false, false, false
			main.teamMenu[side].ratio = false
		end
	end
	main.f_unlock(false)
	local mc = io.open('woo/test/selshot/motif_check.txt', 'w') -- motif values select_shot.py reports
	if mc then
		mc:write(string.format('p1.name.num=%s p2.name.num=%s', tostring(motif.select_info.p1.name.num),
			tostring(motif.select_info.p2.name.num)))
		mc:close()
	end
	f()
	os.exit()
end)

hook.add('start.f_selectScreen', 'woo_selshot', function()
	n = n + 1
	if n == 45 then screenshot() end -- grid overview
	local step = n - 60
	if step < 0 then return end
	if step % 12 == 0 then
		k = k + 1
		if k > #cells then os.exit() end
		start.c[1].selX, start.c[1].selY = cells[k][1], cells[k][2]
	elseif step % 12 == 9 then
		screenshot()
	end
end)
