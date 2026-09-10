# Redis broker — Stage 4 crash-durability baseline

Этот каталог не заменяет отдельный проект HA Redis. Он фиксирует минимальные
параметры брокера, необходимые для приёмочного сценария `kill -9 Redis` без
молчаливой потери уже принятых сообщений Celery.

`redis-broker.conf.example` включает AOF и `appendfsync always`, запрещает
eviction broker messages и предполагает отдельный durable filesystem.
Authentication/TLS/bind/network ACL задаются deployment-конфигурацией и не
хранятся здесь как секреты.

Celery задаёт `CELERY_REDIS_VISIBILITY_TIMEOUT=900` по умолчанию в
`config/celery.py`. Значение должно быть **больше 600 секунд** — hard timeout
самой длинной OCR-задачи. Иначе здоровая длительная OCR-задача может стать
видимой брокеру повторно ещё до завершения.

При SIGKILL всего worker сообщения с late ack могут быть повторно доставлены
после visibility timeout. Это at-least-once семантика: повторный вход задачи
допустим, повторный бизнес-эффект — нет. Acceptance проверяет это через
`QueueDrillProbe.delivery_count` и `completion_count`.

При SIGKILL Redis необходимо запускать тот же instance с тем же AOF/data
directory. Если данные восстановлены из другого/пустого каталога, такой тест не
является проверкой crash recovery и должен считаться FAIL.
