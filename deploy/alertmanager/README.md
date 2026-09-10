# Этап 4 — Alertmanager HA и доставка уведомлений

Статус: **reference implementation / receiver endpoints требуют настройки Заказчика**.

Партия 2 уже создаёт Prometheus alert rules. Эта партия добавляет отдельный
Alertmanager HA-контур, чтобы alert firing превращался в доставляемое событие,
а отказ одного Alertmanager не останавливал оповещение.

Рекомендуемый baseline — Alertmanager **0.33.1**. Перед production-развёртыванием
версию следует повторно сверить с корпоративным зеркалом и политикой обновлений.

## Топология

```text
Prometheus
   |  sends every alert to BOTH instances
   +-------------------+-------------------+
                       |                   |
                Alertmanager 1 <----> Alertmanager 2
                  :9093 API/UI       :9093 API/UI
                     \_____ gossip/state :9094 _____/
                       |                   |
                 warning webhook     critical webhook
```

Prometheus не должен отправлять алерты через один load balancer перед
Alertmanager: оба экземпляра указываются напрямую. Alertmanager gossip
реплицирует silences и notification log и выполняет дедупликацию. При сетевом
разделении возможны дубли — это ожидаемое fail-open поведение, предпочтительное
по сравнению с потерей critical notification.

## Сеть

- TCP/9093 — только Prometheus и operations/admin network;
- TCP/UDP 9094 — только между Alertmanager peers (без TLS transport);
- публичный/пользовательский доступ к 9093/9094 запрещён;
- если gossip идёт между площадками/WAN, перед production включить и испытать
  `--cluster.tls-config` с mTLS либо поместить трафик в защищённый overlay/VPN.

## Установка

На каждом из двух monitoring-узлов:

1. создать системного пользователя `alertmanager` без shell;
2. установить бинарник `/usr/local/bin/alertmanager`;
3. скопировать `alertmanager.yml.example` в
   `/etc/bz-get/alertmanager/alertmanager.yml`;
4. скопировать `alertmanager.env.example` в
   `/etc/bz-get/alertmanager/alertmanager.env` и заменить node-local адреса;
5. создать два root-owned файла URL:

```text
/etc/bz-get/alertmanager/warning-webhook.url
/etc/bz-get/alertmanager/critical-webhook.url
```

Права: `root:alertmanager`, режим `0640` (или строже, если модель доставки это
позволяет). URL/токены не хранятся в Git.

6. установить `deploy/systemd/get-alertmanager.service`;
7. `systemctl daemon-reload && systemctl enable --now get-alertmanager`.

## Receiver contract

`alertmanager.yml.example` использует generic webhook. Это намеренно не
привязывает проект к конкретному внешнему продукту: endpoint может быть
корпоративным relay, Mattermost/Telegram bridge, Service Desk gateway и т.п.

Маршруты:

- `severity=critical` → `critical-webhook.url`, первый send после `10s`, repeat
  каждые `30m` пока проблема не устранена;
- `severity=warning` → `warning-webhook.url`, стандартная группировка и repeat
  каждые `4h`;
- `send_resolved=true` — восстановление тоже доставляется.

Critical ингибирует дублирующий warning того же `alertname/cluster/environment/instance`.

## Проверка конфигурации

`validate.sh` проверяет shell/templates и, если установлен `amtool`, выполняет
`amtool check-config` на временной копии с синтетическими URL-файлами.

После установки:

```bash
curl http://ALERTMANAGER1:9093/-/ready
curl http://ALERTMANAGER2:9093/-/ready
curl http://ALERTMANAGER1:9093/api/v2/status
```

На обоих узлах cluster membership должен видеть два peer.

## Synthetic delivery test

После подключения реальных receiver endpoints:

```bash
ALERTMANAGER_URL=http://ALERTMANAGER1:9093 \
  ./deploy/alertmanager/test-alert.sh warning

ALERTMANAGER_URL=http://ALERTMANAGER1:9093 \
  ./deploy/alertmanager/test-alert.sh critical
```

Оператор должен подтвердить:

1. alert появился в обоих Alertmanager;
2. уведомление пришло в ожидаемый канал;
3. critical пришёл по critical route;
4. resolve доставляется после окончания synthetic alert;
5. при остановке одного Alertmanager уведомление всё равно доставляется.

Без подтверждённого synthetic test operational alerting не считается принятым.

## Эскалация

Текущая конфигурация задаёт технические repeat intervals, но не придумывает
организационные SLA подтверждения. До production Заказчик должен утвердить:

- кто получает warning;
- кто получает critical;
- через какое время critical эскалируется следующему уровню;
- нужен ли второй независимый канал (например, Service Desk + Telegram/SMS);
- кто имеет право создавать silences;
- максимальный срок silence без change/incident number.

## Definition of Done

- два Alertmanager работают в HA cluster;
- Prometheus отправляет алерты сразу обоим;
- receiver secrets находятся вне Git;
- warning/critical маршруты испытаны;
- остановка одного Alertmanager не прерывает доставку;
- synthetic firing + resolve подтверждены получателем;
- маршруты/эскалации утверждены Заказчиком;
- cluster/API endpoints не доступны из пользовательской сети.
