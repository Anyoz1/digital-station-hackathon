# H2–H5: реальные артефакты проверки

Дата: 2026-10-01. Подробная методика, acceptance и ограничения:
[docs/H2_H5_REPORT.md](../../docs/H2_H5_REPORT.md).

- `browser-results.json`: основной без-reload realtime-прогон,1147 frames,
  440 frames второго EventSource, phase/occupancy evidence и частоты по mode/speed.
- `shunt-chromium.png`: sim1050, G2L+L1 на H, ядро T2 остаётся на R3.
- `cargo-chromium.png`: sim1275, полный shunt завершён, G2L на C1, L1 на D1.
- `recovery-results.json`: сохранённый started prefix и cursor reset при restart.
- `browser-role-results.json`: viewer direct API403, повторный login, UUID fallback.
- `final-ui-results.json`: промежуточная проверка sim1275→1280.
- `current-length-results.json`: sim1280, исправленный текущий T2 body56/total76.
- `final-backend-browser-results.json`: окончательный код, sim1280→1290 без reload,
  реальный cargo progress,15 running frames,1,3892Гц; никаких pageerrors.
- `final-paused-chromium.png`: финальный State paused sim1290/seq220.
- `database-results.json`: промежуточная DB-проверка sim1280/seq207.
- `database-final-results.json`: финальная DB-проверка sim1290/seq220,
  реконструкция actual journal=current State,220 events/14 checkpoints/12 receipts;
  отдельная чистая migration DB head0002.
- `browser-console-final.log` / `browser-network-final.log`: финальный позитивный
  путь; role-логи отдельно содержат ожидаемый403. Остальные логи относятся к
  предыдущим проходам. `.log` локальны, игнорируются Git.

JSON/screenshots сняты с реального backend, не API-fixture transport.
Основной большой прогон предшествовал исправлению производных Train.length полей;
физические occupancy/groups уже проверялись, финальные JSON подтверждают correction.
Два SSE-клиента в одном Chromium — не тест двух машин. Reception rate не является
подтверждением ingress→paint<500мс. Фиксированный smoke timetable не является
оптимизацией/независимо валидированным планом. Пароли/cookies не включены.
