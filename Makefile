test:
	pytest backend/test -m "not eval and not e2e"

e2e:
	pytest backend/test -m e2e

eval:
	pytest backend/test -m eval