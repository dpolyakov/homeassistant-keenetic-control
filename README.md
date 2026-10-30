# Keenetic Control

[Русский](#русский) · [English](#english)

![Keenetic Control](custom_components/keenetic_control/brand/icon.png)

## Русский

Keenetic Control — пользовательская интеграция Home Assistant для локального мониторинга и управления роутерами Keenetic и Netcraze через API. Интеграция опрашивает роутер по локальной сети и не отправляет телеметрию во внешние сервисы. Для каждого роутера создайте отдельную запись интеграции.

### Возможности

- Мониторинг доступности роутера и интернета, прошивки, CPU, памяти, времени работы, температуры, WAN и физических портов.
- Отдельные устройства для выбранных клиентов, USB-модемов, Mesh-узлов и поддерживаемых VPN-интерфейсов.
- Управление выбранными клиентами: IP-политика, ограничение пропускной способности, Wake on LAN и отслеживание присутствия.
- Диагностика USB-модемов, перезапуск модема, переключатель мобильного подключения, а при поддержке модема — SMS и USSD.
- События смены WAN-интерфейса и новых SMS; действия Home Assistant для SMS, USSD и запросов RCI.
- Управление включёнными сетями Wi-Fi по SSID в настройках интеграции.

Интеграция создаёт сущности по данным, которые возвращает роутер. Если поле отсутствует или ответ неполный, соответствующая сущность может быть недоступна. Диагностические сущности включены по умолчанию; их можно отключить в реестре Home Assistant.

### Совместимость

- Home Assistant Core **2026.9.0** или новее.
- Целевые версии KeeneticOS — 4 и 5. Формы RCI изучались по ответам KN-1810 с KeeneticOS 4.3.5 и KN-3812 с KeeneticOS 5.1.7. Это не матрица совместимости всех моделей. Набор функций зависит от модели, прошивки, установленных компонентов и доступных ответов RCI; данные моделей Netcraze отдельно не проверялись.

### Установка

#### Через HACS

Если Keenetic Control ещё не опубликован в каталоге HACS, добавьте репозиторий вручную:

1. В HACS откройте меню в правом верхнем углу и выберите **Custom repositories**.
2. Укажите `https://github.com/dpolyakov/homeassistant-keenetic-control`, выберите тип **Integration** и нажмите **Add**.
3. Найдите **Keenetic Control**, откройте страницу интеграции и нажмите **Download**.
4. Перезапустите Home Assistant.

Подробнее: [добавление пользовательского репозитория](https://hacs.xyz/docs/faq/custom_repositories/) и [установка интеграции через HACS](https://hacs.xyz/docs/use/repositories/type/integration/).

#### Вручную

Скопируйте каталог `custom_components/keenetic_control` из репозитория в `<config>/custom_components/keenetic_control`, где `<config>` — каталог конфигурации Home Assistant. Перезапустите Home Assistant.

### Добавление роутера

1. В Home Assistant откройте **Settings → Devices & services → Add integration**.
2. Найдите **Keenetic Control**.
3. Укажите адрес роутера и порт RCI. По умолчанию используются `http://192.168.1.1` и порт `80`. В адресе не указывайте путь или порт — для порта есть отдельное поле.
4. Введите имя пользователя и пароль, если роутер требует авторизацию. Заполните оба поля или оставьте оба пустыми, если доступ к RCI уже настроен без авторизации.

Для каждого роутера повторите добавление интеграции. Home Assistant определяет роутер по MAC-адресу.

**Ограничьте доступ к RCI локальной сетью и доверенными устройствами.** При подключении по HTTPS интеграция не проверяет сертификат роутера. Не открывайте RCI в интернет.

### Настройки интеграции

- Интервал опроса — 30 секунд по умолчанию, минимум 1 секунда.
- Списки выбранных клиентов: один для трекинга, другой для управления IP-политиками. Клиентов можно выбрать из обнаруженных или добавить по MAC-адресу.
- Список включённых сетей Wi-Fi по SSID. **При сохранении интеграция включает выбранные сети и выключает остальные точки доступа из списка.**
- Разрешение произвольных POST-запросов RCI — выключено по умолчанию. Такой запрос может изменить настройки роутера или запустить команду.
- Номер SMS-получателя по умолчанию.

### Устройства и сущности

| Устройство | Основные данные и управление |
| --- | --- |
| Роутер | Доступность и интернет, системные показатели, маршрут WAN и скорость, состояние физических портов, смонтированный USB-накопитель, DNS Proxy, IPsec VICI, перезагрузка |
| Клиенты | Сущности создаются только для выбранных MAC-адресов. Доступны трекинг, сведения о подключении, IP-политика, Bandwidth Limit, Wake on LAN и очистка ограничения |
| USB-модемы | Состояние подключения, доступная LTE-диагностика и квота, переключатель мобильного подключения, перезапуск, SMS и USSD при поддержке модема |
| Mesh-узлы | Доступные системные показатели, backhaul и состояния портов |
| VPN | OpenVPN, PPTP, SSTP, WireGuard и ZeroTier; для WireGuard также доступны данные peer, если их возвращает RCI |

Некоторые показатели появляются только при наличии соответствующих полей или компонентов. Например, квота модема требует включённого счётчика на роутере. Отсутствующие и неоднозначные данные не подменяются нулём.

### События и действия

Интеграция публикует события `keenetic_control_wan_changed` и `keenetic_control_sms`. Первое содержит `previous_interface` и `interface`. Второе появляется для новых полных SMS и включает текст сообщения.

Пример уведомления о смене WAN:

```yaml
triggers:
  - trigger: event
    event_type: keenetic_control_wan_changed
actions:
  - action: notify.mobile_app_phone
    data:
      message: "WAN: {{ trigger.event.data.interface or 'нет' }}"
```

Доступны действия `keenetic_control.send_sms`, `keenetic_control.list_sms`, `keenetic_control.mark_sms_read`, `keenetic_control.delete_sms`, `keenetic_control.send_ussd` и `keenetic_control.request_api`, а также кнопки перезагрузки роутера и перезапуска модема. SMS- и USSD-действия требуют выбрать ровно одно устройство модема. `list_sms` не помечает сообщения прочитанными.

Пример отправки SMS:

```yaml
actions:
  - action: keenetic_control.send_sms
    target:
      device_id: "MODEM_DEVICE_ID"
    data:
      message: "Проверка связи"
      recipient: "+79990000000"
```

`recipient` задаёт один номер; если его не указать, используется номер из настроек интеграции. Для нескольких получателей добавьте отдельное действие для каждого номера. Действие сообщает о принятии команды роутером, но не подтверждает доставку SMS. Если результат изменяющего запроса неизвестен, интеграция не повторяет его автоматически.

USSD-запрос может выполняться до 60 секунд. На одном роутере одновременно обрабатывается один запрос USSD.

`request_api` выполняет GET-запросы к RCI. POST-запросы доступны только после включения соответствующей настройки и могут менять состояние роутера. Проверяйте путь и данные перед вызовом.

Текст SMS передаётся в событии Home Assistant и может сохраниться в данных автоматизаций и журналах. Учитывайте это при настройке хранения данных.

### Ограничения

- Доступность функций зависит от модели, KeeneticOS, установленных компонентов и ответа RCI.
- Резервное копирование и обновление прошивки, управление пробросом портов, Wi-Fi QR-коды и управление питанием USB-порта не реализованы.
- Для Mesh пока не создаются сенсоры температуры и Wi-Fi-трафика, а также сущности USB-накопителей.
- Изменяющие действия, включая перезагрузку и переключение сетей, выполняйте с учётом их влияния на подключение роутера и клиентов.

### Поддержка и подробности

- [Сообщить о проблеме](https://github.com/dpolyakov/homeassistant-keenetic-control/issues)
- [Источники и ограничения RCI](docs/official-sources.md)
- [Решение о модели сущностей](docs/adr/0001-useful-home-assistant-entity-model.md)

## English

Keenetic Control is a custom Home Assistant integration for monitoring and controlling Keenetic and Netcraze routers over the local RCI interface. It polls the router on the local network and does not send telemetry to external services. Add a separate integration entry for each router.

### Features

- Monitor router and internet availability, firmware, CPU, memory, uptime, temperature, WAN, and physical ports.
- Create separate devices for selected clients, USB modems, Mesh nodes, and supported VPN interfaces.
- Manage selected clients: IP policies, bandwidth limits, Wake on LAN, and presence tracking.
- Inspect USB modem diagnostics, restart a modem, enable or disable its mobile interface, and use SMS and USSD when the modem supports them.
- Receive WAN-change and new-SMS events; use Home Assistant actions for SMS, USSD, and RCI requests.
- Select enabled Wi-Fi networks by SSID in the integration options.

The integration creates entities from the data returned by the router. An entity may be unavailable when its field is missing or the response is incomplete. Diagnostic entities are enabled by default and can be disabled in the Home Assistant entity registry.

### Compatibility

- Home Assistant Core **2026.9.0** or later.
- The target router software versions are KeeneticOS 4 and 5. RCI response formats were reviewed using a KN-1810 running KeeneticOS 4.3.5 and a KN-3812 running KeeneticOS 5.1.7. These examples do not establish compatibility with every model. Available features depend on the model, firmware, installed components, and RCI responses. Netcraze-branded models have not been separately verified.

### Installation

#### With HACS

If Keenetic Control is not yet listed in the HACS catalog, add the repository as a custom repository:

1. In HACS, open the menu in the top-right corner and select **Custom repositories**.
2. Enter `https://github.com/dpolyakov/homeassistant-keenetic-control`, select **Integration**, and click **Add**.
3. Find **Keenetic Control**, open its page, and select **Download**.
4. Restart Home Assistant.

See the HACS guides for [adding a custom repository](https://hacs.xyz/docs/faq/custom_repositories/) and [using an integration from HACS](https://hacs.xyz/docs/use/repositories/type/integration/).

#### Manual installation

Copy `custom_components/keenetic_control` from this repository to `<config>/custom_components/keenetic_control`, where `<config>` is your Home Assistant configuration directory. Restart Home Assistant.

### Add a router

1. In Home Assistant, open **Settings → Devices & services → Add integration**.
2. Search for **Keenetic Control**.
3. Enter the router address and RCI port. The defaults are `http://192.168.1.1` and port `80`. Enter the port in its separate field; do not include a path or port in the address.
4. Enter the username and password if the router requires authentication. Fill in both fields or leave both empty if unauthenticated RCI access is already configured.

Repeat these steps for each router. Home Assistant identifies a router by its MAC address.

**Restrict RCI access to your local network and trusted hosts.** The integration does not validate the router certificate when connecting over HTTPS. Do not expose RCI to the internet.

### Integration options

- Polling interval: 30 seconds by default, with a minimum of 1 second.
- Separate client lists for presence tracking and IP-policy controls. Select discovered clients or enter MAC addresses manually.
- Enabled Wi-Fi networks by SSID. **When you save the options, selected networks are enabled and other access points in the list are disabled.**
- Arbitrary RCI POST requests are disabled by default. A POST request may change router settings or start a command.
- Default SMS recipient.

### Devices and entities

| Device | Main data and controls |
| --- | --- |
| Router | Availability and internet status, system metrics, WAN route and rate, physical port status, mounted USB storage, DNS Proxy, IPsec VICI, reboot |
| Clients | Entities are created only for selected MAC addresses. Available features include tracking, connection details, IP policy, bandwidth limit, Wake on LAN, and clearing the limit |
| USB modems | Connection state, available LTE diagnostics and quota, mobile-interface switch, restart, SMS, and USSD when supported by the modem |
| Mesh nodes | Available system metrics, backhaul, and port status |
| VPN | OpenVPN, PPTP, SSTP, WireGuard, and ZeroTier; WireGuard peer data is available when returned by RCI |

Some sensors require specific fields or router components. For example, modem quota entities require the router's traffic counter to be enabled. Missing or ambiguous data is not reported as zero.

### Events and actions

The integration publishes `keenetic_control_wan_changed` and `keenetic_control_sms` events. The WAN event contains `previous_interface` and `interface`. The SMS event is emitted for new complete messages and includes the message text.

Example notification for a WAN change:

```yaml
triggers:
  - trigger: event
    event_type: keenetic_control_wan_changed
actions:
  - action: notify.mobile_app_phone
    data:
      message: "WAN: {{ trigger.event.data.interface or 'none' }}"
```

Available actions are `keenetic_control.send_sms`, `keenetic_control.list_sms`, `keenetic_control.mark_sms_read`, `keenetic_control.delete_sms`, `keenetic_control.send_ussd`, and `keenetic_control.request_api`. The integration also provides buttons to reboot the router and restart a modem. SMS and USSD actions require exactly one modem device as the target. `list_sms` does not mark messages as read.

Example SMS action:

```yaml
actions:
  - action: keenetic_control.send_sms
    target:
      device_id: "MODEM_DEVICE_ID"
    data:
      message: "Connection check"
      recipient: "+12025550123"
```

`recipient` accepts one number. If omitted, the integration uses the default recipient from its options. Add a separate action for each additional recipient. The action reports that the router accepted the command; it does not confirm SMS delivery. If the result of a state-changing request is unknown, the integration does not retry it automatically.

USSD requests can take up to 60 seconds. Only one USSD request runs per router at a time.

`request_api` performs RCI GET requests. POST requests require the corresponding option to be enabled and may change router state. Check the endpoint and request data before calling the action.

SMS text is included in the Home Assistant event and may be stored in automation data and logs. Account for this when configuring data retention.

### Limitations

- Feature availability depends on the router model, KeeneticOS version, installed components, and RCI response.
- Backup and firmware updates, port-forwarding controls, Wi-Fi QR codes, and USB-port power management are not implemented.
- Mesh temperature and Wi-Fi traffic sensors, and USB storage entities for Mesh nodes, are not implemented.
- State-changing actions, including rebooting and changing Wi-Fi networks, can affect router and client connectivity.

### Support and further reading

- [Report an issue](https://github.com/dpolyakov/homeassistant-keenetic-control/issues)
- [RCI sources and limitations](docs/official-sources.md)
- [Entity model decision](docs/adr/0001-useful-home-assistant-entity-model.md)
