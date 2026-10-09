/* SAFAR operations PWA. Private data stays in memory; the journal is canonical. */
(() => {
'use strict';
const app = document.getElementById('app');
const demo = new URLSearchParams(location.search).get('demo') === '1';
let language = 'uk';
try { language = localStorage.getItem('safar.locale') === 'ru' ? 'ru' : 'uk'; } catch (_) {}
const words = {
 returns:['Повернення','Возвраты'], returnNew:['Зафіксувати повернення','Зафиксировать возврат'], returnReason:['Причина повернення','Причина возврата'], returnHelp:['Заявка створюється вручну. Це не скасування ТТН і не доказ доставки товару назад.','Запись создаётся оператором. Это не удаление ТТН и не доказательство фактического возврата.'], returnSaved:['Повернення додано до окремого журналу.','Возврат добавлен в отдельный журнал.'], returnEmpty:['Повернень у цьому чаті не зареєстровано.','В этом чате возвраты пока не зарегистрированы.'], returnWarehouse:['Склад: очікуємо підтвердження','Склад: ожидает подтверждения'], returnUnknown:['Стан перевізника не перевірений','Статус перевозчика не проверен'], intake:['Додати замовлення','Добавить заказ'], intakeTitle:['Розумний прийом замовлення','Умный приём заказа'], intakeHelp:['Встав текст замовлення. SAFAR сам розпізнає поля та перевірить їх перед створенням ТТН.','Вставьте текст заказа. SAFAR сам извлечёт поля и проверит их перед созданием ТТН.'], intakeSource:['Текст замовлення','Текст заказа'], intakeSubmit:['Передати в автоматичну обробку','Передать на автоматическую обработку'], intakeDisabled:['Автоматичний прийом замовлень ще не дозволено адміністратором.','Автоматический приём заказов ещё не разрешён администратором.'], intakeImagesLater:['Фото та скриншоти: підключення захищеного сховища ще не завершене.','Фото и скриншоты: защищённое хранилище ещё не подключено.'], intakeQueued:['Замовлення збережено й передано в чергу.','Заказ сохранён и передан в очередь.'], intakeExisting:['Замовлення вже отримане; повтор не створений.','Заказ уже получен; повтор не создан.'], intakeEmpty:['Встав текст одного замовлення.','Вставьте текст одного заказа.'], radar:['Оперативний контроль','Оперативный контроль'], radarDesc:['Перевірені статуси Нової пошти для останніх ТТН. Перевірка виконується лише коли застосунок відкритий.','Проверенные статусы Новой Почты для последних ТТН. Проверка выполняется только при открытом приложении.'], radarNoStatus:['Перевірка ще не завершена','Проверка ещё не завершена'], radarError:['Статус тимчасово недоступний','Статус временно недоступен'], radarRefresh:['Оновити статуси','Обновить статусы'], home:['Головна','Главная'], orders:['Замовлення','Заказы'], shipments:['ТТН','ТТН'], senders:['Відправники','Отправители'], settings:['Налаштування','Настройки'],
 control:['Контроль відправок','Контроль отправок'], overviewDesc:['Замовлення, фото й накладні — з вашого Telegram-журналу.','Заказы, фото и накладные — из вашего Telegram-журнала.'],
 all:['Усі','Все'], fresh:['Нові','Новые'], processing:['В обробці','В обработке'], created:['ТТН створено','ТТН создана'], invalid:['Потрібна перевірка','Нужна проверка'], failed:['Помилка','Ошибка'], uncertain:['Перевірити у НП','Проверить в НП'], deleted:['Видалено','Удалено'],
 attention:['Потребують уваги','Требуют внимания'], total:['Всього замовлень','Всего заказов'], journal:['У вашому журналі','В вашем журнале'], issued:['Оформлені документи','Оформленные документы'], unresolved:['Помилки та перевірки','Ошибки и проверки'], queued:['Очікують завершення','Ожидают завершения'],
 heroTitle:['Кожне замовлення.','Каждый заказ.'], heroSecond:['Під контролем.','Под контролем.'], heroDesc:['Фото залишаються із замовленням. Історія — із відправленням.','Фото остаются с заказом. История — с отправлением.'], openOrders:['Відкрити замовлення','Открыть заказы'], recent:['Остання активність','Последняя активность'], seeAll:['Усі замовлення →','Все заказы →'],
 demoNotice:['Вигадані дані. Дії працюють лише в цьому перегляді; реальні ТТН не створюються.','Вымышленные данные. Действия работают только в этом просмотре; реальные ТТН не создаются.'],
 connected:['Синхронізовано','Синхронизировано'], syncing:['Оновлення','Обновление'], offline:['Без мережі','Без сети'], lastSync:['Останнє оновлення','Последнее обновление'], noSync:['Ще не синхронізовано','Ещё не синхронизировано'], unavailable:['Немає зв’язку','Нет связи'],
 refresh:['Оновити','Обновить'], retry:['Спробувати ще раз','Попробовать ещё раз'], loading:['Завантаження…','Загрузка…'], loadMore:['Показати ще','Показать ещё'], shown:['Показано','Показано'], of:['із','из'], records:['записів','записей'],
 inboxDesc:['Фото, адреса й суми. Фільтри шукають у всьому журналі.','Фото, адрес и суммы. Фильтры ищут во всём журнале.'], registry:['Реєстр замовлень','Реестр заказов'], search:['Пошук за ім’ям, містом, ТТН…','Поиск по имени, городу, ТТН…'], searchLabel:['Пошук замовлень','Поиск заказов'],
 noOrders:['Поки немає замовлень','Пока нет заказов'], noOrdersDesc:['Перешли фото та текст замовлення авторизованому боту SAFAR у вибраному чаті.','Перешлите фото и текст заказа авторизованному боту SAFAR в выбранном чате.'], noMatch:['Нічого не знайдено','Ничего не найдено'], noMatchDesc:['Спробуй інший запит або скинь фільтри.','Попробуйте другой запрос или сбросьте фильтры.'], clearFilters:['Скинути фільтри','Сбросить фильтры'],
 loadError:['Не вдалося завантажити дані','Не удалось загрузить данные'], networkError:['Перевір мережу та повтори спробу. Збережені замовлення не змінені.','Проверьте сеть и повторите попытку. Сохранённые заказы не изменены.'], expired:['Сесія завершилась. Увійди знову через Telegram.','Сессия завершилась. Войдите снова через Telegram.'], denied:['Дія недоступна для цього доступу. Онови сесію та перевір дозволений чат.','Действие недоступно для этого доступа. Обновите сессию и проверьте разрешённый чат.'], notFound:['Запис недоступний у вибраному чаті.','Запись недоступна в выбранном чате.'], conflict:['Замовлення вже змінилося. Онови картку та перевір актуальні дані перед збереженням.','Заказ уже изменился. Обновите карточку и проверьте актуальные данные перед сохранением.'], validation:['Перевір поля: ПІБ, телефон, відділення, суми та вагу.','Проверьте поля: ФИО, телефон, отделение, суммы и вес.'], rateLimit:['Забагато запитів. Зачекай хвилину та повтори спробу.','Слишком много запросов. Подождите минуту и повторите попытку.'],
 period:['Активність','Активность'], anyTime:['За весь час','За всё время'], week:['Останні 7 днів','Последние 7 дней'], month:['Останні 30 днів','Последние 30 дней'], today:['Сьогодні','Сегодня'], senderFilter:['Відправник','Отправитель'], allSenders:['Усі відправники','Все отправители'], sort:['Порядок','Порядок'], newest:['Спочатку нові','Сначала новые'], oldest:['Спочатку давні','Сначала старые'], scope:['Робочий чат','Рабочий чат'],
 recipient:['Одержувач','Получатель'], city:['Місто','Город'], area:['Область','Область'], warehouse:['Відділення НП','Отделение НП'], branch:['Відділення','Отделение'], unknownCity:['Місто не визначено','Город не определён'], declared:['Оголошена вартість','Объявленная стоимость'], cod:['Післяплата','Наложенный платёж'], phone:['Телефон','Телефон'], weight:['Вага, кг','Вес, кг'], description:['Опис товару','Описание товара'], unnamed:['Замовлення без ПІБ','Заказ без ФИО'],
 back:['До замовлень','К заказам'], backShipments:['До накладних','К накладным'], detail:['Картка замовлення','Карточка заказа'], productPhotos:['Товари та фото','Товары и фото'], photos:['фото','фото'], noPhotos:['До цього замовлення фото не додано','К этому заказу фото не добавлены'], photo:['Фото товару','Фото товара'], photoOpen:['Відкрити фото','Открыть фото'], photoError:['Фото недоступне','Фото недоступно'], retryPhoto:['Повторити фото','Повторить фото'], previous:['Попереднє фото','Предыдущее фото'], next:['Наступне фото','Следующее фото'], close:['Закрити','Закрыть'],
 waybill:['Накладна / ТТН','Накладная / ТТН'], noTtn:['Ще не створено','Ещё не создана'], copy:['Скопіювати ТТН','Скопировать ТТН'], copied:['ТТН скопійовано','ТТН скопирована'], copyFailed:['Буфер недоступний. Виділи номер і скопіюй вручну.','Буфер недоступен. Выделите номер и скопируйте вручную.'], carrierLink:['Відкрити у Новій Пошті','Открыть в Новой Почте'], tracking:['Перевірити доставку','Проверить доставку'], trackingLoading:['Запит до Нової Пошти…','Запрос к Новой Почте…'], trackingUnknown:['Доставку ще не перевірено','Доставку ещё не проверяли'], trackingHint:['Створена ТТН означає оформлений документ. Рух і доставку підтверджує перевізник.','Созданная ТТН означает оформленный документ. Движение и доставку подтверждает перевозчик.'], checked:['Перевірено','Проверено'],
 operations:['Операційна інформація','Операционная информация'], account:['Відправник','Отправитель'], createdAt:['Додано','Добавлено'], updatedAt:['Оновлено','Обновлено'], status:['Стан','Состояние'], source:['Оригінал із Telegram','Оригинал из Telegram'], sourceLink:['Відкрити джерело','Открыть источник'], receiptHistory:['Історія накладних','История накладных'], activityHistory:['Історія замовлення','История заказа'], noHistory:['Додаткових подій поки немає.','Дополнительных событий пока нет.'],
 edit:['Запропонувати виправлення','Предложить исправление'], editHeading:['Безпечне виправлення','Безопасное исправление'], staged:['Є запропоновані виправлення','Есть предложенные исправления'], stagedNotice:['Пропозицію збережено окремо. Чинна ТТН, фотографії та історія не змінені. Застосування — через авторизований Telegram-бот; нова ТТН можлива лише після підтвердженого видалення старої.','Предложение сохранено отдельно. Действующая ТТН, фотографии и история не изменены. Применение — через авторизованный Telegram-бот; новая ТТН возможна только после подтверждённого удаления старой.'], correctionHelp:['Збережеться лише пропозиція. Ця дія не створює ТТН та не запускає чергу.','Сохранится только предложение. Это действие не создаёт ТТН и не запускает очередь.'], editLocked:['Під час обробки або невизначеного результату зміни заблоковані. Перевір стан у Telegram; автоматичне повторення заборонене.','Во время обработки или неопределённого результата изменения заблокированы. Проверьте состояние в Telegram; автоматическое повторение запрещено.'], review:['Перевірити зміни','Проверить изменения'], current:['Зараз','Сейчас'], proposed:['Пропозиція','Предложение'], save:['Зберегти пропозицію','Сохранить предложение'], saving:['Збереження…','Сохранение…'], cancel:['Скасувати','Отменить'], noChanges:['Зміни поля перед збереженням.','Измените поля перед сохранением.'], saved:['Пропозицію збережено. ТТН не змінено.','Предложение сохранено. ТТН не изменена.'],
 shipmentDesc:['Пошук збережених накладних. Статус доставки — тільки від перевізника.','Поиск сохранённых накладных. Статус доставки — только от перевозчика.'], documents:['Збережені накладні','Сохранённые накладные'], noShipments:['Поки немає накладних','Пока нет накладных'], noShipmentsDesc:['Тут з’являться ТТН, які створить чинний Telegram-бот.','Здесь появятся ТТН, которые создаст действующий Telegram-бот.'],
 senderDesc:['Вибір стосується лише майбутніх замовлень у цьому чаті.','Выбор касается только будущих заказов в этом чате.'], selected:['Обраний','Выбран'], primary:['Основний','Основной'], configured:['Налаштований','Настроен'], unconfigured:['Не підключено','Не подключён'], selectSender:['Для нових замовлень','Для новых заказов'], senderSelected:['Відправника змінено для майбутніх замовлень.','Отправитель изменён для будущих заказов.'], fopPending:['Кабінет ФОП: підключення очікується','Кабинет ФОП: подключение ожидается'], senderPrivacy:['Телефон не підтверджує доступ до кабінету. Потрібні перевірена API-авторизація й офіційні реквізити НП. Попередні відправлення зберігають свого відправника.','Телефон не подтверждает доступ к кабинету. Нужны проверенная API-авторизация и официальные реквизиты НП. Предыдущие отправления сохраняют своего отправителя.'], noSenders:['Немає доступних відправників','Нет доступных отправителей'],
 analytics:['Операційна аналітика','Операционная аналитика'], analyticsDesc:['Дані журналу. ТТН не прирівнюємо до доставки, суми замовлень — до виручки.','Данные журнала. ТТН не приравниваем к доставке, суммы заказов — к выручке.'], activity:['Замовлення за днями','Заказы по дням'], processingTime:['Середній час обробки','Среднее время обработки'], noMetrics:['Ще недостатньо даних','Пока недостаточно данных'], seconds:['с','с'], minutes:['хв','мин'],
 preferences:['Безпека, мова й підключення.','Безопасность, язык и подключение.'], session:['Сесія та доступ','Сессия и доступ'], accessType:['Тип доступу','Тип доступа'], verified:['Підтверджений Telegram','Подтверждённый Telegram'], synthetic:['Вигаданий демонстраційний журнал','Вымышленный демонстрационный журнал'], expires:['Сесія діє до','Сессия действует до'], logout:['Вийти із сесії','Выйти из сессии'], loggedOut:['Сесію завершено','Сессия завершена'], locale:['Мова інтерфейсу','Язык интерфейса'], diagnostics:['Підключення','Подключение'], install:['Встановити SAFAR','Установить SAFAR'], installHelp:['Android: меню браузера → «Встановити застосунок» або «Додати на головний екран». iPhone: Safari → «Поділитися» → «На початковий екран».','Android: меню браузера → «Установить приложение» или «Добавить на главный экран». iPhone: Safari → «Поделиться» → «На экран Домой».'], installed:['Застосунок встановлено','Приложение установлено'], privacy:['У сховищі браузера — лише мова й оболонка застосунку. Замовлення, фото та сесійні ключі не кешуються. Для особистих даних потрібна мережа.','В хранилище браузера — только язык и оболочка приложения. Заказы, фото и сессионные ключи не кешируются. Для личных данных нужна сеть.'], offlineHint:['Показано останні дані цієї сесії. Оновлення, фото та збереження потребують мережі.','Показаны последние данные этой сессии. Обновление, фото и сохранение требуют сети.'],
 privateSpace:['Захищений простір','Защищённое пространство'], loginHelp:['Відкрий /app у приватному чаті з авторизованим Telegram-ботом або підключи цей пристрій одноразовим кодом.','Откройте /app в личном чате с авторизованным Telegram-ботом или подключите это устройство одноразовым кодом.'], pair:['Підключити пристрій','Подключить устройство'], pairing:['Підключення пристрою','Подключение устройства'], pairInstruction:['Надішли боту лише код із цього власного пристрою. Не підтверджуй чужі коди. Код одноразовий; цей екран завершить вхід автоматично.','Отправьте боту только код с этого собственного устройства. Не подтверждайте чужие коды. Код одноразовый; этот экран завершит вход автоматически.'], pairExpires:['Код діє до','Код действует до'], pairWaiting:['Очікуємо підтвердження в Telegram…','Ожидаем подтверждения в Telegram…'], pairExpired:['Код завершився. Створи новий.','Код истёк. Создайте новый.'], pairCheck:['Перевірити підтвердження','Проверить подтверждение'], demoButton:['Переглянути демо','Посмотреть демо'], secured:['Лише авторизовані оператори','Только авторизованные операторы'],
 closeEdit:['Закрити виправлення','Закрыть исправление'], changes:['Запропоновані зміни','Предложенные изменения'], original:['Початкові дані','Исходные данные'], readOnly:['Лише перегляд','Только просмотр'],
};
const t = key => words[key] ? words[key][language === 'ru' ? 1 : 0] : key;
const esc = value => String(value ?? '').replace(/[&<>"']/g, char => ({'&':'&amp;','<':'&lt;','>':'&gt;','"':'&quot;',"'":'&#39;'}[char]));
const icons = {
 home:'<path d="m3 9 9-7 9 7v11a2 2 0 0 1-2 2H5a2 2 0 0 1-2-2zM9 22V12h6v10"/>',
 orders:'<rect x="4" y="4" width="16" height="16" rx="2"/><path d="M8 8h8M8 12h8M8 16h5"/>',
 box:'<path d="m12 2 9 5-9 5-9-5 9-5ZM3 7v10l9 5 9-5V7M12 12v10"/>',
 users:'<path d="M16 21v-2a4 4 0 0 0-4-4H6a4 4 0 0 0-4 4v2M22 21v-2a4 4 0 0 0-3-3.87M16 3.13a4 4 0 0 1 0 7.75"/><circle cx="9" cy="7" r="4"/>',
 settings:'<circle cx="12" cy="12" r="3"/><path d="m12 2 2 3 4-1 2 4-2 3 2 3-2 4-4-1-2 3-2-3-4 1-2-4 2-3-2-3 2-4 4 1 2-3Z"/>',
 search:'<circle cx="10.5" cy="10.5" r="7.5"/><path d="m16 16 5 5"/>',
 arrow:'<path d="m9 18 6-6-6-6"/>', back:'<path d="m15 18-6-6 6-6"/>', close:'<path d="m6 6 12 12M6 18 18 6"/>',
 bell:'<path d="M18 8a6 6 0 0 0-12 0c0 7-3 7-3 9h18c0-2-3-2-3-9M10 21h4"/>',
 photo:'<rect x="3" y="3" width="18" height="18" rx="3"/><circle cx="8.5" cy="8.5" r="1.5"/><path d="m21 15-5-5L5 21"/>',
 shield:'<path d="m12 22 7-4V7l-7-4-7 4v11l7 4zM9 12l2 2 4-4"/>',
 clock:'<circle cx="12" cy="12" r="9"/><path d="M12 7v5l3 2"/>',
 truck:'<path d="M1 5h14v12H1zM15 9h4l4 4v4h-8"/><circle cx="5" cy="19" r="2"/><circle cx="19" cy="19" r="2"/>',
 logout:'<path d="M9 21H5a2 2 0 0 1-2-2V5a2 2 0 0 1 2-2h4M16 17l5-5-5-5M21 12H9"/>',
 copy:'<rect x="8" y="8" width="13" height="13" rx="2"/><path d="M16 8V5a2 2 0 0 0-2-2H5a2 2 0 0 0-2 2v9a2 2 0 0 0 2 2h3"/>',
 refresh:'<path d="M3 11a9 9 0 0 1 15-6l3 3M21 3v5h-5M21 13a9 9 0 0 1-15 6l-3-3M3 21v-5h5"/>',
 lock:'<rect x="5" y="10" width="14" height="12" rx="2"/><path d="M8 10V7a4 4 0 1 1 8 0v3"/>',
 edit:'<path d="m15 5 4 4M4 20l4-1L20 7a2.8 2.8 0 0 0-4-4L4 15v5Z"/>',
 link:'<path d="M14 3h7v7M21 3 10 14M10 3H5a2 2 0 0 0-2 2v14a2 2 0 0 0 2 2h14a2 2 0 0 0 2-2v-5"/>',
 trend:'<path d="M3 3v18h18M7 15l5-5 4 3 5-7"/>', wifi:'<path d="M2 8a16 16 0 0 1 20 0M5 12a11 11 0 0 1 14 0M8 16a6 6 0 0 1 8 0M12 20h.01"/>',
 check:'<path d="m5 12 4 4L19 6"/>', download:'<path d="M12 3v12m-5-5 5 5 5-5M3 16v5h18v-5"/>'
};
const icon = (name, cls='') => `<svg class="${cls}" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="1.7" stroke-linejoin="round" stroke-linecap="round" aria-hidden="true">${icons[name] || icons.box}</svg>`;
const timestamp = value => { const n = Number(value); if (Number.isFinite(n) && n > 0) return new Date(n < 1e11 ? n * 1000 : n); const parsed = new Date(value); return !Number.isNaN(parsed.getTime()) ? parsed : null; };
const locale = () => language === 'ru' ? 'ru-UA' : 'uk-UA';
const dateStr = value => { const d = timestamp(value); return d ? new Intl.DateTimeFormat(locale(), {day:'2-digit',month:'short',hour:'2-digit',minute:'2-digit',timeZone:'Europe/Kyiv'}).format(d) : '—'; };
const money = value => value === null || value === undefined || value === '' || !Number.isFinite(Number(value)) ? '—' : new Intl.NumberFormat(locale(), {maximumFractionDigits:2}).format(Number(value)) + ' ₴';
const stateName = status => t({collecting:'fresh',processing:'processing',created:'created',invalid:'invalid',failed:'failed',uncertain:'uncertain',deleted:'deleted'}[status] || 'invalid');
const badge = status => `<span class="state state-${['collecting','processing','created','invalid','failed','uncertain','deleted'].includes(status) ? status : 'invalid'}">${stateName(status)}</span>`;
const tabs = [['home','home','home'],['orders','orders','orders'],['shipments','shipments','truck'],['returns','returns','refresh'],['senders','senders','users'],['settings','settings','settings']];
const view = {tab:'home',returnTab:'orders',filter:'all',search:'',sender:'',period:'all',sort:'updated_desc',chatId:null,scopes:[],selected:null,orders:[],counts:{},pagination:{},authed:false,loading:true,listLoading:false,moreLoading:false,error:'',detailLoading:false,detailError:'',detailCache:{},tracking:{},trackingLoading:false,trackingError:'',senders:null,sendersLoading:false,sendersError:'',analytics:null,analyticsLoading:false,analyticsError:'',csrf:'',expiresAt:null,lastSync:null,offline:!navigator.onLine || document.body.dataset.offlineShell === 'true',editing:false,editDraft:null,editError:'',saving:false,pair:null,pairBusy:false,pairError:'',photoErrors:new Set(),photoAttempts:{},photo:null,installPrompt:null,scopeLoading:false};
view.returnCases = []; view.returnTracking = {}; view.returnTrackingBusy = {}; view.returnTrackingErrors = {}; view.returnsLoading = false; view.returnsError = '';
view.carrierEvents = {}; view.isAdmin = false; view.archiveView = 'active'; view.autoIntakeEnabled = false; view.mediaIntakeEnabled = false; view.ocrEnabled = false; view.radarLoading = false; view.radarErrors = {};
let requestSequence = 0, sessionEpoch = 0, expiryTimer = null, listController = null, searchTimer = null, pairingTimer = null, pairSequence = 0;
let sessionChannel;
try { sessionChannel = new BroadcastChannel('safar-session'); sessionChannel.onmessage = e => { if (e.data === 'logout') clearSession(); }; } catch (_) {}
// Fictitious fixtures are isolated in the browser; no API write occurs in demo mode.
const demoNow = Date.now() / 1000;
const demoOrders = [
 {id:'preview-01',state:'created',recipient:'Марія Коваленко',city:'Одеса',area:'Одеська',warehouse:'18',declared:3850,cod:0,ttn:'PREVIEW-0001',sender_profile:'default',phone:'+380000000001',description:'Кросівки · 1 пара',photos:[{index:0},{index:1}],weight:1.2,art:'shoe'},
 {id:'preview-02',state:'invalid',recipient:'Олена Демченко',city:'Київ',area:'Київська',warehouse:'8',declared:2190,cod:0,ttn:'',sender_profile:'default',phone:'',description:'Худі · чорний',photos:[{index:0},{index:1},{index:2}],weight:1,art:'hoodie',error:'У вигаданому замовленні потрібно уточнити телефон.'},
 {id:'preview-03',state:'processing',recipient:'Андрій Мельник',city:'Львів',area:'Львівська',warehouse:'41',declared:4990,cod:2500,ttn:'',sender_profile:'default',phone:'+380000000003',description:'Черевики · 43 розмір',photos:[{index:0}],weight:1.5,art:'shoe'},
 {id:'preview-04',state:'created',recipient:'Тетяна Романюк',city:'Дніпро',area:'Дніпропетровська',warehouse:'11',declared:1200,cod:0,ttn:'PREVIEW-0004',sender_profile:'default',phone:'+380000000004',description:'Сумка · шкіра',photos:[{index:0}],weight:0.8,art:'bag'},
 {id:'preview-05',state:'collecting',recipient:'Віктор Савченко',city:'Чернівці',area:'Чернівецька',warehouse:'5',declared:920,cod:0,ttn:'',sender_profile:'default',phone:'+380000000005',description:'Футболка',photos:[{index:0}],weight:0.5,art:'hoodie'},
 {id:'preview-06',state:'failed',recipient:'Ірина Бондар',city:'Вінниця',area:'Вінницька',warehouse:'3',declared:1690,cod:0,ttn:'',sender_profile:'default',phone:'+380000000006',description:'Кеди · білі',photos:[{index:0}],weight:1,art:'shoe'},
].map((o, i) => ({...o,created_at:demoNow - (i + 1) * 3100,updated_at:demoNow - i * 2500,revision:'demo-' + i,source_text:`Демонстраційне замовлення\n${o.recipient}\n${o.city}, відділення ${o.warehouse}\nОцінка: ${o.declared}\nПісляплата: ${o.cod}`,history:[],receipts:o.ttn ? [{ttn:o.ttn,status:'active',created_at:demoNow - (i + 1) * 3000,sender_profile:'default'}] : []}));
function demoArt(order, index=0) {
 const hoodie = '<path d="m52 60 25-14c4-21 42-21 46 0l25 14 22 45-23 12-15-31v73H68V86l-15 31-23-12 22-45Z" fill="#383638" stroke="#8f8688" stroke-width="2"/><path d="M77 47c0 32 46 32 46 0M84 68v26m32-26v26M82 133h36" stroke="#a69a9d" stroke-width="2" fill="none"/>';
 const shoe = '<path d="m40 126 13-42c3-9 15-8 20-2l21 23 29 4 26 11 12 5c13 4 13 20-1 23H41c-13 0-15-16-1-22Z" fill="#c5bdb2" stroke="#e9e3da" stroke-width="2"/><path d="M34 137h130M66 94l18 10m-23 0 19 11m-23 0 18 11M112 112l-9 20 25 1 7-15" stroke="#57474b" stroke-width="4" fill="none"/>';
 const bag = '<path d="m50 78 100 0 10 80H40l10-80Z" fill="#5a343d" stroke="#af737d" stroke-width="2"/><path d="M77 84V62c0-29 46-29 46 0v22" fill="none" stroke="#b58088" stroke-width="7"/><path d="M50 112h100" stroke="#84525d" stroke-width="2"/><rect x="91" y="104" width="18" height="15" rx="2" fill="#c3ac89"/>';
 return `<svg viewBox="0 0 200 200" class="demo-product" aria-hidden="true"><rect width="200" height="200" fill="${index % 2 ? '#29272b' : '#252128'}"/><path d="M0 165 200 112v88H0Z" fill="#15171b"/><ellipse cx="100" cy="164" rx="65" ry="8" fill="#07090c" opacity=".6"/>${order.art === 'hoodie' ? hoodie : order.art === 'bag' ? bag : shoe}<text x="12" y="23" fill="#b5a2a7" font-size="9" font-family="system-ui" letter-spacing="2">SAFAR / DEMO</text></svg>`;
}
function button(action, label, ico='refresh', disabled=false, cls='button-quiet', extra='') { return `<button type="button" class="${cls}" data-action="${action}" ${disabled ? 'disabled' : ''} ${extra}>${icon(ico)}<span>${esc(label)}</span></button>`; }
const kv = (label, value) => `<div class="keyval"><span class="keyval-label">${esc(label)}</span><span class="keyval-value">${value}</span></div>`;
const title = (eyebrow, heading, desc) => `<div class="page-header"><div><div class="eyebrow">${eyebrow}</div><h1 tabindex="-1">${esc(heading)}</h1><div class="page-subtitle">${esc(desc)}</div></div></div>`;
const totalCounts = () => { const c = view.counts; return {all:Object.values(c).reduce((sum, n) => sum + (Number(n) || 0), 0),new:Number(c.collecting) || 0,attention:['invalid','failed','uncertain'].reduce((sum,s) => sum + (Number(c[s]) || 0),0),created:Number(c.created) || 0,processing:Number(c.processing) || 0}; };
function currentScope() { const scope = view.scopes.find(s => String(s.chat_id) === String(view.chatId)); return scope ? scope.label : ''; }
function connectionLabel() { return demo ? 'DEMO' : view.offline ? t('offline') : view.listLoading ? t('syncing') : view.error ? t('unavailable') : view.lastSync ? t('connected') : t('noSync'); }
function topbar() {
 return `<header class="topbar"><div class="mobile-header"><div class="mobile-brand">SAFAR</div><div class="mobile-caption">SHIPPING<br>OPERATIONS OS</div></div><div class="top-date">SAFAR / OPERATIONS / ${new Intl.DateTimeFormat(locale(), {day:'2-digit',month:'long',year:'numeric',timeZone:'Europe/Kyiv'}).format(new Date())}</div><div class="top-actions"><span class="status-chip ${view.offline || view.error ? 'status-disconnected' : ''}"><span class="dot"></span>${esc(connectionLabel())}</span>${button('refresh',t('refresh'),'refresh',view.listLoading || view.offline,'icon-button',`aria-label="${esc(t('refresh'))}"`)}</div></header>`;
}
function sidebar() {
 return `<aside class="sidebar"><div class="brand"><div class="brand-name">SAFAR</div><div class="brand-caption">SHIPPING OPERATIONS OS</div><div class="brand-line"></div></div><div class="aside-label">CONTROL CENTER</div><nav aria-label="${esc(t('orders'))}">${tabs.map(tab => `<button type="button" class="nav-link ${view.tab === tab[0] ? 'active' : ''}" data-tab="${tab[0]}" ${view.tab === tab[0] ? 'aria-current="page"' : ''}>${icon(tab[2])}${esc(t(tab[1]))}</button>`).join('')}</nav><div class="sidebar-footer"><div class="connection">${icon('shield')}<span>${demo ? 'DEMO · PRIVATE PREVIEW' : esc(t('secured'))}</span></div><div class="foot-sub">SAFAR APP · V0.2</div></div></aside>`;
}
function bottom() { return `<nav class="bottom-nav" aria-label="SAFAR">${tabs.map(tab => `<button type="button" class="bottom-item ${view.tab === tab[0] ? 'active' : ''}" data-tab="${tab[0]}" ${view.tab === tab[0] ? 'aria-current="page"' : ''}>${icon(tab[2])}<span>${esc(t(tab[1]))}</span></button>`).join('')}</nav>`; }
function empty(heading, desc, action='') { return `<div class="empty">${icon('box')}<h3>${esc(heading)}</h3><p>${esc(desc)}</p>${action}</div>`; }
function errorPanel(message, action='refresh') { return `<div class="error-panel" role="alert">${icon('wifi')}<div><strong>${esc(t('loadError'))}</strong><p>${esc(message || t('networkError'))}</p></div>${button(action,t('retry'),'refresh',view.offline)}</div>`; }
function skeleton(rows=3) { return `<div class="skeleton-list" aria-label="${esc(t('loading'))}" aria-busy="true">${Array.from({length:rows}, () => '<div class="skeleton-row"><div class="skeleton-photo"></div><div class="skeleton-copy"><i></i><i></i></div><div class="skeleton-badge"></div></div>').join('')}</div>`; }
function safePhotoURL(photo) {
 if (!photo || typeof photo.url !== 'string') return '';
 try { const url = new URL(photo.url,location.origin); if (url.origin !== location.origin || !url.pathname.startsWith('/api/safar/orders/') || !/\/photo\/\d+$/.test(url.pathname)) return ''; if (view.chatId !== null) url.searchParams.set('chat_id',String(view.chatId)); return url.pathname + url.search; } catch (_) { return ''; }
}
function photoContent(order, index, full=false) {
 if (demo) return demoArt(order,index);
 const url = safePhotoURL((order.photos || [])[index]);
 const key = `${order.id}:${index}`;
 if (!url || view.photoErrors.has(key)) return `<div class="photo-fallback">${icon('photo')}<span>${esc(t('photoError'))}</span>${full ? button('photo-retry',t('retryPhoto'),'refresh',view.offline) : ''}</div>`;
 const attempt = view.photoAttempts[key] || 0;
 return `<img src="${esc(url + (attempt ? (url.includes('?') ? '&' : '?') + 'attempt=' + attempt : ''))}" data-photo-key="${esc(key)}" alt="${esc(t('photo'))} ${index + 1}" ${full ? 'fetchpriority="high"' : 'loading="lazy"'} decoding="async">`;
}
function imagePreview(order,index=0,cls='order-img') {
 const photos = order.photos || [];
 if (!photos.length) return `<div class="${cls} photo-empty">${icon('photo')}<span class="sr-only">${esc(t('noPhotos'))}</span></div>`;
 return `<div class="${cls} has-photo">${photoContent(order,index)}${cls === 'order-img' && photos.length > 1 ? `<span class="photo-count">${icon('photo')}${photos.length}</span>` : ''}</div>`;
}
function oneOrder(order) {
 return `<button type="button" class="order-row ${['invalid','failed','uncertain'].includes(order.state) ? 'order-attention' : ''}" data-order="${esc(order.id)}">${imagePreview(order)}<div class="order-main"><div class="order-name">${esc(order.recipient || t('unnamed'))}${order.source === 'app' ? '<span class="source-chip">APP</span>' : ''}</div><div class="order-place">${esc([order.city,order.warehouse ? `${t('branch')} №${order.warehouse}` : ''].filter(Boolean).join(' · ') || t('unknownCity'))}</div><div class="order-extra"><span>${esc(dateStr(order.updated_at || order.created_at))}</span>${order.has_pending_edits || order.pending_order ? `<span class="draft-dot" title="${esc(t('staged'))}">${icon('edit')}</span>` : ''}</div></div><div class="order-meta"><div class="order-money">${money(order.declared)}</div><div class="order-cod">${esc(t('cod'))}: ${money(order.cod)}</div>${badge(order.state)}</div><span class="order-arrow" aria-hidden="true">›</span></button>`;
}
function panelOrders(data,limit=Infinity,shipment=false) {
 if (view.listLoading && !data.length) return skeleton();
 if (!data.length) { const filtered = view.search || view.filter !== 'all' || view.sender || view.period !== 'all'; return empty(t(filtered ? 'noMatch' : shipment ? 'noShipments' : 'noOrders'),t(filtered ? 'noMatchDesc' : shipment ? 'noShipmentsDesc' : 'noOrdersDesc'),filtered ? button('clear-filters',t('clearFilters'),'close') : ''); }
 return `<div class="orders-list" ${view.listLoading ? 'aria-busy="true"' : ''}>${data.slice(0,limit).map(oneOrder).join('')}</div>`;
}
const stat = (label,value,sub,ico,hot=false) => `<div class="metric ${hot ? 'hot' : ''}"><div class="metric-top"><span>${esc(label)}</span><span class="metric-icon">${icon(ico)}</span></div><div class="metric-num">${esc(value)}</div><div class="metric-foot">${esc(sub)}</div></div>`;
function analyticsPanel() {
 if (view.analyticsLoading && !view.analytics) return `<div class="panel analytics-panel"><h3>${esc(t('analytics'))}</h3>${skeleton(1)}</div>`;
 if (view.analyticsError && !view.analytics) return `<div class="panel analytics-panel"><h3>${esc(t('analytics'))}</h3>${errorPanel(view.analyticsError,'refresh-analytics')}</div>`;
 const a = view.analytics || {}, daily = Array.isArray(a.daily_activity) ? a.daily_activity : Array.isArray(a.daily) ? a.daily : [];
 const values = daily.slice(-14).map(day => ({date:day.date || day.day || '',count:Number(day.count ?? day.total) || 0}));
 const max = Math.max(1,...values.map(d => d.count));
 return `<div class="panel analytics-panel"><div class="panel-header"><h3>${esc(t('analytics'))}</h3>${icon('trend')}</div><div class="analytics-caption">${language === 'ru' ? 'Активность журнала' : 'Активність журналу'} · ${daily.length || 7} ${language === 'ru' ? 'дней' : 'днів'}</div>${values.length ? `<div class="bar-chart" role="img" aria-label="${esc(t('activity') + ': ' + values.map(d => d.date + ' — ' + d.count).join(', '))}">${values.map(d => `<div class="chart-column" title="${esc(d.date)}: ${d.count}"><span>${d.count || ''}</span><i style="--bar-height:${Math.max(3,d.count / max * 100)}%"></i><small>${esc(d.date.slice(-2))}</small></div>`).join('')}</div>` : `<div class="chart-empty">${esc(t('noMetrics'))}</div>`}${kv(language === 'ru' ? 'Сохранённые ТТН' : 'Збережені ТТН',esc(a.with_ttn ?? '—'))}<p class="analytics-note">${esc(t('analyticsDesc'))}</p></div>`;
}
function home() {
 const c = totalCounts();
 const ru = language === 'ru';
 const seen = Boolean(view.lastSync);
 const intakeStatus = view.autoIntakeEnabled
  ? (ru ? 'Доступен приём заказов в приложении' : 'Приймання замовлень у застосунку доступне')
  : (ru ? 'Заказы принимаются через Telegram. Приём из приложения пока выключен.' : 'Замовлення приймаються через Telegram. Приймання із застосунку поки вимкнено.');
 const lanes = [
  {target:'orders',code:'01',symbol:'orders',head:ru?'ОЧЕРЕДЬ ЗАКАЗОВ':'ЧЕРГА ЗАМОВЛЕНЬ',caption:ru?'Проверка данных, фото и ошибок':'Перевірка даних, фото та помилок',value:seen?c.attention:'—'},
  {target:'shipments',code:'02',symbol:'truck',head:ru?'КОНТРОЛЬ ТТН':'КОНТРОЛЬ ТТН',caption:ru?'Оформленные отправки и статусы НП':'Оформлені відправлення і статуси НП',value:seen?c.created:'—'},
  {target:'returns',code:'03',symbol:'refresh',head:ru?'ВОЗВРАТЫ И СКЛАД':'ПОВЕРНЕННЯ І СКЛАД',caption:ru?'Обратные ТТН, приёмка, расходы':'Зворотні ТТН, приймання, витрати',value:Array.isArray(view.returnCases)?view.returnCases.length:'—'}
 ];
 return `<div class="screen control-dashboard">
  ${title('SAFAR / CONTROL CENTER 03',t('control'),t('overviewDesc'))}
  <div class="hero hero-v031">
   <div class="hero-copy">
    <div class="eyebrow hero-kicker"><span class="live-pip" aria-hidden="true"></span> CONTROL / RELEASE 03.2</div>
    <h2>${esc(t('heroTitle'))}<br><span>${esc(t('heroSecond'))}</span></h2>
    <p>${esc(t('heroDesc'))}</p>
    <div class="hero-actions">
      <button type="button" class="button-primary" data-tab="orders">${icon('orders')}${esc(t('openOrders'))}${icon('arrow')}</button>
      ${button('open-intake',t('intake'),'box',view.offline,'button-quiet hero-secondary')}
    </div>
   </div>
   <div class="hero-art hero-monogram" aria-hidden="true"><div class="hero-monogram-name">S<span>/</span>F</div><span>PRIVATE OPERATIONS<br>V 0.3.2</span></div>
   <span class="hero-watermark" aria-hidden="true">03</span>
  </div>
  <div class="metrics metrics-v031">
   ${stat(t('total'),seen?c.all:'—',t('journal'),'orders',true)}
   ${stat(t('attention'),seen?c.attention:'—',t('unresolved'),'bell')}
   ${stat(t('created'),seen?c.created:'—',t('issued'),'truck')}
   ${stat(t('processing'),seen?c.processing:'—',t('queued'),'clock')}
  </div>
  <section class="control-operations" aria-label="${esc(ru?'Быстрый доступ':'Швидкий доступ')}">
   <div class="operations-head"><div><div class="eyebrow">ACTION / COMMANDS</div><h2>${esc(ru?'Рабочий центр':'Робочий центр')}</h2></div><span class="release-pill">SAFAR CONTROL 03</span></div>
   <div class="operations-grid">${lanes.map(l=>`<button type="button" class="operation-card" data-tab="${l.target}"><span class="operation-top"><span class="operation-code">${l.code} / ${l.head}</span>${icon(l.symbol)}</span><span class="operation-main"><span class="operation-value">${esc(l.value)}</span>${icon('arrow')}</span><span class="operation-desc">${l.caption}</span></button>`).join('')}</div>
  </section>
  <div class="intake-status" role="status"><span class="intake-state-dot ${view.autoIntakeEnabled?'enabled':''}" aria-hidden="true"></span><span>${esc(intakeStatus)}</span><button type="button" data-tab="settings">${esc(t('settings'))} ${icon('arrow')}</button></div>
  ${view.error?errorPanel(view.error):''}
  <div class="grid-panels"><div class="panel"><div class="panel-header"><h3>${esc(t('recent'))}</h3><button type="button" class="section-more" data-tab="orders">${esc(t('seeAll'))}</button></div>${panelOrders(view.orders,5)}</div>${analyticsPanel()}</div>
 </div>`;
}
function filters() {
 const c = totalCounts();
 const profiles = view.senders?.profiles || [];
 return `<div class="toolbar"><label class="search">${icon('search')}<input id="orderSearch" type="search" autocomplete="off" maxlength="120" aria-label="${esc(t('searchLabel'))}" placeholder="${esc(t('search'))}" value="${esc(view.search)}"></label>${button('refresh',t('refresh'),'refresh',view.listLoading || view.offline,'button-quiet toolbar-refresh',`aria-label="${esc(t('refresh'))}"`)}</div><div class="chip-list">${[['all',`${t('all')} · ${c.all}`],['collecting',`${t('fresh')} · ${c.new}`],['processing',t('processing')],['created',`${t('created')} · ${c.created}`],['attention',`${t('attention')} · ${c.attention}`]].map(([value,label]) => `<button type="button" class="filter-chip ${view.filter === value ? 'active' : ''}" aria-pressed="${view.filter === value}" data-filter="${value}">${esc(label)}</button>`).join('')}</div><div class="filter-controls"><label>${esc(t('period'))}<select id="dateFilter">${[['all','anyTime'],['today','today'],['7','week'],['30','month']].map(([v,k]) => `<option value="${v}" ${view.period === v ? 'selected' : ''}>${esc(t(k))}</option>`).join('')}</select></label><label>${esc(t('senderFilter'))}<select id="senderFilter"><option value="">${esc(t('allSenders'))}</option>${profiles.map(p => `<option value="${esc(p.id)}" ${view.sender === p.id ? 'selected' : ''}>${esc(p.label || p.id)}</option>`).join('')}</select></label><label>${esc(t('sort'))}<select id="sortFilter"><option value="updated_desc" ${view.sort === 'updated_desc' ? 'selected' : ''}>${esc(t('newest'))}</option><option value="updated_asc" ${view.sort === 'updated_asc' ? 'selected' : ''}>${esc(t('oldest'))}</option></select></label></div>`;
}
function shipmentRadar() {
 const issued=view.orders.filter(o=>o.ttn && /^\d{14}$/.test(o.ttn)).slice(0,3);
 if (!issued.length) return "";
 return `<div class="panel radar-panel"><div class="panel-header"><div><div class="eyebrow">SAFAR / CARRIER RADAR</div><h3>${esc(t("radar"))}</h3></div>${button("radar-refresh",t(view.radarLoading?"trackingLoading":"radarRefresh"),"refresh",view.radarLoading || view.offline)}</div><p class="muted-text">${esc(t("radarDesc"))}</p><div class="radar-list">${issued.map(o=>{const tdata=view.tracking[o.id];const verified=tdata?.tracking || tdata; const message=demo ? t("demoNotice") : verified?.status || verified?.status_text || t(view.radarErrors[o.id]?"radarError":"radarNoStatus");return `<button type="button" class="radar-row" data-order="${esc(o.id)}"><span class="radar-dot ${verified?.phase==="delivered"?"radar-complete":""}"></span><span class="radar-name"><strong>${esc(o.recipient)}</strong><small class="selectable">${esc(o.ttn)}</small></span><span class="radar-status"><strong>${esc(message)}</strong><small>${verified?.checked_at ? esc(dateStr(verified.checked_at)) : "—"}</small></span></button>`;}).join("")}</div></div>`;
}
function orderList(shipment=false) {
 const page = view.pagination, total = Number(page.total);
 const count = Number.isFinite(total) ? total : view.orders.length;
 return `<div class="screen">${title(shipment ? 'LOGISTICS / SHIPMENTS' : 'WORKSPACE / ORDER MANAGEMENT',t(shipment ? 'shipments' : 'orders'),t(shipment ? 'shipmentDesc' : 'inboxDesc'))}${filters()}${view.isAdmin ? `<div class="archive-switch" role="group" aria-label="${esc(language==='ru'?'Просмотр записей':'Перегляд записів')}"><button type="button" data-action="show-active" class="${view.archiveView==='active'?'selected':''}" aria-pressed="${view.archiveView==='active'}">${esc(language==='ru'?'Активные заказы':'Активні замовлення')}</button><button type="button" data-action="show-archive" class="${view.archiveView==='archived'?'selected':''}" aria-pressed="${view.archiveView==='archived'}">${icon('lock')}${esc(language==='ru'?'Архив':'Архів')} · ${Number(view.counts.archived||0)}</button></div>` : ''}${view.archiveView==='archived' ? `<p class="archive-disclaimer">${esc(language==='ru'?'Это архив SAFAR, не удаление ТТН в Новой Почте. Записи можно восстановить.':'Це архів SAFAR, а не видалення ТТН у Новій пошті. Записи можна відновити.')}</p>` : ''}${view.error ? errorPanel(view.error) : ''}${shipment ? shipmentRadar() : ''}<div class="panel"><div class="panel-header"><h3>${esc(t(shipment ? 'documents' : 'registry'))}</h3><span class="panel-small">${count} ${esc(t('records'))}</span>${button('open-intake',t('intake'),'orders',view.offline,'button-primary')}</div>${panelOrders(view.orders,Infinity,shipment)}${view.orders.length ? `<div class="pagination"><span>${esc(t('shown'))} ${view.orders.length} ${esc(t('of'))} ${count}</span>${page.has_more ? button('load-more',t(view.moreLoading ? 'loading' : 'loadMore'),'arrow',view.moreLoading || view.offline) : ''}</div>` : ''}</div></div>`;
}
const fieldDefs = [['full_name','recipient','text','recipient'],['phone','phone','tel','phone'],['city','city','text','city'],['warehouse','warehouse','text','warehouse'],['cost','declared','text','declared'],['cod_amount','cod','text','cod'],['weight','weight','text','weight'],['description','description','text','description']];
function editPanel(order) {
 if (['processing','uncertain'].includes(order.state)) return `<div class="detail-block"><h3>${esc(t('editHeading'))}</h3><p class="muted-text">${esc(t('editLocked'))}</p></div>`;
 if (!view.editing) return `<div class="detail-block"><h3>${esc(t('editHeading'))}</h3>${order.correction_stale ? `<div class="form-error" role="alert">${esc(t('conflict'))}</div>` : ''}<p class="muted-text">${esc(t('correctionHelp'))}</p>${button('edit',t('edit'),'edit',view.detailLoading || view.offline || (!demo && !order.revision),'button-primary')}${order.pending_order ? `<div class="notice">${icon('shield')}<div><strong>${esc(t('staged'))}</strong><p>${esc(t('stagedNotice'))}</p></div></div>` : ''}</div>`;
 const draft = view.editDraft || {};
 return `<div class="detail-block edit-panel"><div class="detail-head"><h3>${esc(t('editHeading'))}</h3>${button('cancel-edit',t('closeEdit'),'close',view.saving,'icon-button',`aria-label="${esc(t('closeEdit'))}"`)}</div><p class="muted-text">${esc(t('correctionHelp'))}</p><form id="correctionForm" autocomplete="off"><div class="edit-grid">${fieldDefs.map(([key,,type,label]) => `<label for="edit-${key}">${esc(t(label))}<input id="edit-${key}" name="${key}" type="${type}" value="${esc(draft[key])}" maxlength="${key === 'description' || key === 'city' ? 100 : key === 'full_name' ? 140 : 32}" ${['cost','cod_amount','weight','warehouse'].includes(key) ? 'inputmode="decimal"' : ''} ${key === 'phone' ? 'inputmode="tel"' : ''} ${view.saving ? 'disabled' : ''}></label>`).join('')}</div>${view.editError ? `<div class="form-error" role="alert">${esc(view.editError)}</div>` : ''}<div class="form-actions"><button type="submit" data-action="save-correction" class="button-primary" ${view.saving || view.offline ? 'disabled' : ''}>${icon('shield')}${esc(t('review'))}</button>${button('cancel-edit',t('cancel'),'close',view.saving)}</div></form></div>`;
}
function trackingPanel(order) {
 const data = view.tracking[order.id];
 const tracking = data?.tracking || data;
 const status = tracking?.status_text || tracking?.status || tracking?.description || tracking?.message;
 return `<div class="tracking-result" aria-live="polite"><div class="eyebrow">${esc(t('readOnly'))} / NOVA POSHTA</div><strong>${esc(view.trackingLoading ? t('trackingLoading') : typeof status === 'string' ? status : t('trackingUnknown'))}</strong>${tracking?.checked_at || tracking?.fetched_at ? `<span>${esc(t('checked'))}: ${esc(dateStr(tracking.checked_at || tracking.fetched_at))}</span>` : ''}${view.trackingError ? `<p class="form-error" role="alert">${esc(view.trackingError)}</p>` : ''}<p>${esc(t('trackingHint'))}</p></div>`;
}
function historyPanel(order) {
 const receipts = Array.isArray(order.receipts) ? order.receipts : [];
 const history = Array.isArray(order.history) ? order.history : [];
 const carrier = view.carrierEvents[order.id]?.timeline;
 const carrierEntries=Array.isArray(carrier?.events)?carrier.events:[];
 const carrierMarkup=carrierEntries.length ? `<div class="divider"></div><h3>${esc(language==="ru"?"История статусов перевозчика":"Історія статусів перевізника")}</h3><p class="muted-text">${esc(language==="ru"?"Подтверждённые наблюдения Новой Почты; не означают приёмку складом или выплату.":"Перевірені спостереження Нової пошти; не підтверджують приймання складом або виплату.")}</p><ol class="timeline">${carrierEntries.map(event=>`<li><span class="timeline-dot"></span><strong>${esc(event.status)}</strong><p>${esc(dateStr(event.at))} · NOVA POSHTA</p></li>`).join("")}</ol>` : "";
 return `<div class="detail-block"><h3>${esc(t('receiptHistory'))}</h3>${receipts.length ? `<ol class="timeline">${receipts.map(receipt => `<li><span class="timeline-dot"></span><div><strong class="selectable">${esc(receipt.ttn || '—')}</strong><p>${esc(receipt.sender_profile || order.sender_profile || '—')} · ${esc(dateStr(receipt.deleted_at || receipt.created_at || receipt.created || receipt.updated_at))}</p><span class="state ${receipt.state === 'deleted' || receipt.status === 'deleted' || receipt.deleted ? 'state-deleted' : 'state-created'}">${esc(receipt.state === 'deleted' || receipt.status === 'deleted' || receipt.deleted ? t('deleted') : t('created'))}</span></div></li>`).join('')}</ol>` : `<p class="muted-text">${esc(t('noHistory'))}</p>`}${history.length ? `<div class="divider"></div><h3>${esc(t('activityHistory'))}</h3><ol class="timeline">${history.slice(-12).reverse().map(event => `<li><span class="timeline-dot"></span><div><strong>${esc(stateName(event.state || event.status) || event.action || '—')}</strong><p>${esc(dateStr(event.deleted_at || event.at || event.created_at || event.time))}</p>${event.ttn ? `<span class="selectable">${esc(event.ttn)}</span>` : ''}</div></li>`).join('')}</ol>` : ''}${carrierMarkup}</div>`;
}
function safeSource(value) { return typeof value === 'string' && /^https:\/\/t\.me\/c\/\d+\/\d+$/.test(value) ? value : ''; }
function detail() {
 const order = view.detailCache[view.selected] || view.orders.find(o => o.id === view.selected);
 const back = `<button type="button" class="detail-back" data-action="back">${icon('back')}${esc(t(view.returnTab === 'shipments' ? 'backShipments' : 'back'))}</button>`;
 if (!order || (view.detailLoading && !view.detailCache[view.selected])) return `<div class="screen">${back}${title('ORDER / DETAIL',t('detail'),t('loading'))}${view.detailError ? errorPanel(view.detailError,'detail-retry') : skeleton(4)}</div>`;
 const photos = order.photos || [], source = safeSource(order.source_url);
 const ttnURL = !demo && /^\d{14}$/.test(order.ttn || '') ? `https://novaposhta.ua/tracking/?cargo_number=${encodeURIComponent(order.ttn)}` : '';
 return `<div class="screen">${back}${title('ORDER / DETAIL',t('detail'),[order.recipient,order.city].filter(Boolean).join(' · '))}${view.detailError ? errorPanel(view.detailError,'detail-retry') : ''}<div class="details-grid"><div><div class="detail-block"><div class="detail-head"><h3>${esc(t('productPhotos'))}</h3>${badge(order.state)}</div><div class="eyebrow">${photos.length} ${esc(t('photos'))} / TELEGRAM</div>${photos.length ? `<div class="gallery">${photos.map((_,index) => `<button type="button" class="gallery-item has-photo" data-photo="${index}" aria-label="${esc(t('photoOpen'))} ${index + 1}">${photoContent(order,index)}</button>`).join('')}</div>` : `<div class="no-photo">${icon('photo')}${esc(t('noPhotos'))}</div>`}<div class="divider"></div>${kv(t('description'),esc(order.description || '—'))}<div class="amounts"><div><span>${esc(t('declared'))}</span><strong>${money(order.declared)}</strong></div><div><span>${esc(t('cod'))}</span><strong>${money(order.cod)}</strong></div></div></div><div class="detail-block"><h3>${esc(t('recipient'))}</h3>${kv(t('recipient'),esc(order.recipient || '—'))}${kv(t('phone'),esc(order.phone || '—'))}${kv(t('city'),esc(order.city || '—'))}${kv(t('area'),esc(order.area || '—'))}${kv(t('warehouse'),esc(order.warehouse || '—'))}${kv(t('weight'),esc(order.weight || '—'))}</div>${order.source_text || source ? `<div class="detail-block"><h3>${esc(t('source'))}</h3>${order.source_text ? `<pre class="source-text">${esc(order.source_text)}</pre>` : ''}${source ? `<a class="button-quiet source-link" href="${esc(source)}" target="_blank" rel="noopener noreferrer">${icon('link')}${esc(t('sourceLink'))}</a>` : ''}</div>` : ''}${historyPanel(order)}</div><div><div class="detail-block ttn-block"><div class="detail-head"><h3>${esc(t('waybill'))}</h3>${icon('truck')}</div><div class="eyebrow">NOVA POSHTA</div><p class="ttn-large selectable">${esc(order.ttn || t('noTtn'))}</p>${badge(order.state)}${order.ttn ? `<div class="ttn-actions">${button('copy',t('copy'),'copy',false,'button-quiet',`data-ttn="${esc(order.ttn)}"`)}${ttnURL ? `<a class="button-quiet" href="${esc(ttnURL)}" target="_blank" rel="noopener noreferrer">${icon('link')}${esc(t('carrierLink'))}</a>` : ''}${order.can_print && !demo ? `<a class="button-quiet" href="${esc(scopedURL('/api/safar/orders/' + encodeURIComponent(order.id) + '/pdf'))}" rel="noopener noreferrer" download>${icon('download')}${esc(language==='ru'?'Скачать PDF ТТН':'Завантажити PDF ТТН')}</a>` : ''}${button('track',t(view.trackingLoading ? 'trackingLoading' : 'tracking'),'refresh',view.trackingLoading || view.offline)}</div>${trackingPanel(order)}` : `<p class="muted-text">${esc(t('trackingHint'))}</p>`}</div>${editPanel(order)}${view.isAdmin && !demo ? `<div class="detail-block archive-admin-panel"><div class="eyebrow">SAFAR / ADMIN CONTROL</div><h3>${esc(language==='ru'?'Управление записью':'Керування записом')}</h3><p class="muted-text">${esc(language==='ru'?'Архивирование скрывает заказ из активного списка. ТТН, история и данные клиента сохраняются; в Новой Почте ничего не удаляется.':'Архівування приховує замовлення з активного списку. ТТН, історія та дані клієнта зберігаються; у Новій пошті нічого не видаляється.')}</p>${order.archived ? `<span class="archive-status">${icon('lock')}${esc(language==='ru'?'В архиве':'В архіві')}</span>` : ''}${button('open-archive',order.archived?(language==='ru'?'Восстановить заказ':'Відновити замовлення'):(language==='ru'?'Убрать из списка':'Прибрати зі списку'),order.archived?'refresh':'lock',view.offline || !order.can_archive || view.saving,'button-quiet archive-admin-button')}</div>` : ''}${order.state === 'created' ? `<div class="detail-block"><h3>${esc(t('returns'))}</h3><p class="muted-text">${esc(t('returnHelp'))}</p>${button('open-return',t('returnNew'),'refresh',view.offline,'button-quiet')}</div>` : ''}<div class="detail-block"><h3>${esc(t('operations'))}</h3>${kv(t('account'),esc(order.sender_profile || '—'))}${kv(t('createdAt'),esc(dateStr(order.created_at)))}${kv(t('updatedAt'),esc(dateStr(order.updated_at)))}${kv(t('status'),esc(stateName(order.state)))}${order.error || order.notice ? `<div class="notice">${esc(order.error || order.notice)}</div>` : ''}</div></div></div></div>`;
}
function senders() {
 const data = view.senders, profiles = data?.profiles || [];
 const content = view.sendersLoading && !data ? skeleton(2) : view.sendersError ? errorPanel(view.sendersError,'refresh-senders') : profiles.length ? profiles.map(profile => {
 const selected = profile.id === data.selected_profile || profile.selected;
 return `<div class="sender-card"><div class="sender-identity"><div class="sender-mark">${esc((profile.label || profile.id || 'S').charAt(0).toUpperCase())}</div><div><div class="eyebrow">${profile.primary || profile.id === data.primary_profile ? esc(t('primary')) : 'SENDER PROFILE'}</div><div class="sender-label">${esc(profile.label || profile.id)}</div><div class="sender-desc">${esc(profile.id)} · ${esc(t(profile.configured ? 'configured' : 'unconfigured'))}</div></div></div>${selected ? `<span class="state state-created">${icon('check')}${esc(t('selected'))}</span>` : button('select-sender',t('selectSender'),'arrow',!profile.configured || view.offline || view.sendersLoading,'button-quiet',`data-profile="${esc(profile.id)}"`)}</div>`;
 }).join('') : empty(t('noSenders'),t('senderPrivacy'));
 return `<div class="screen">${title('IDENTITY / ACCOUNTS',t('senders'),t('senderDesc'))}${content}${!profiles.some(p => p.id === 'fop' && p.configured) ? `<div class="fop-pending">${icon('lock')}<div><strong>${esc(t('fopPending'))}</strong><p>${esc(t('senderPrivacy'))}</p></div></div>` : `<div class="notice">${esc(t('senderPrivacy'))}</div></div>`}`;
}
function scopeControl() { return view.scopes.length > 1 ? `<label class="scope-control"><span>${esc(t('scope'))}</span><select id="scopeFilter">${view.scopes.map(scope => `<option value="${esc(scope.chat_id)}" ${String(scope.chat_id) === String(view.chatId) ? 'selected' : ''}>${esc(scope.label)} · ${Number(scope.order_count) || 0}</option>`).join('')}</select></label>` : ''; }
function returnScreen() {
 const cases=view.returnCases || [];
 const labels={refused_by_recipient:language==="ru"?"Отказ получателя":"Відмова одержувача",unclaimed:language==="ru"?"Не забрали":"Не забрали",easy_return_after_delivery:language==="ru"?"Лёгкий возврат":"Легке повернення",customer_exchange:language==="ru"?"Обмен":"Обмін",other:language==="ru"?"Другая причина":"Інша причина"};
 const item=c=>{
  const received=c.warehouse_state==="received";
  const warehouseLabel=received?(language==="ru"?"Принято на складе":"Прийнято на складі"):t("returnWarehouse");
  const financeLabel=language==="ru"?"Финансы не подтверждены":"Фінанси не підтверджені";
  const tr=view.returnTracking[c.id], busy=!!view.returnTrackingBusy[c.id], err=view.returnTrackingErrors[c.id];
  const latest=tr && tr.status ? `<div class="return-carrier" role="status"><div class="eyebrow">NOVA POSHTA / ${esc(t("checked"))} ${esc(dateStr(tr.checked_at))}</div><strong>${esc(tr.status)}</strong><p>${esc(warehouseLabel)}</p></div>` : `<p class="muted-text">${esc(t("returnUnknown"))}</p>`;
  return `<article class="return-case-card"><div class="return-case-head"><div><div class="eyebrow">REVERSE / CASE</div><h3>${esc(labels[c.reason] || labels.other)}</h3><p class="selectable">${esc(c.outbound_ttn)}</p></div><span class="state ${received?"state-created":"state-uncertain"}">${esc(warehouseLabel)}</span></div><div class="return-case-meta">${esc(dateStr(c.created_at))} · ${esc(c.sender_profile)}</div><div class="return-finance-state">${icon("shield")}<span>${esc(financeLabel)}${c.expense_total_kopeks>0 ? ` · ${esc(language==="ru"?"Расходы (со слов оператора)":"Витрати (зі слів оператора)")}: ${esc((c.expense_total_kopeks / 100).toFixed(2))} ₴` : ""}</span></div>${latest}${err ? `<p class="form-error" role="alert">${esc(err)}</p>` : ""}<div class="return-case-actions">${button("track-return",t(busy ? "trackingLoading" : "tracking"),"refresh",busy || view.offline,"button-quiet",`data-case-id="${esc(c.id)}"`)}${button("open-expense",language==="ru"?"Записать расход":"Записати витрати","edit",view.offline || demo,"button-quiet",`data-case-id="${esc(c.id)}"`)}${!received ? button("open-warehouse",language==="ru"?"Принять на склад":"Прийняти на склад","check",view.offline || demo,"button-primary",`data-case-id="${esc(c.id)}"`) : ""}${c.reason==="easy_return_after_delivery" && !c.reverse_ttn ? button("open-easy-link",language==="ru"?"Связать обратную ТТН":"Пов’язати зворотну ТТН","link",view.offline,"button-quiet",`data-case-id="${esc(c.id)}"`) : ""}<button class="button-quiet" data-order="${esc(c.order_id)}">${icon("arrow")}${esc(t("detail"))}</button></div></article>`;
 };
 return `<div class="screen">${title("REVERSE / LOGISTICS",t("returns"),t("returnHelp"))}${view.returnsError ? errorPanel(view.returnsError,"refresh-returns") : ""}<div class="panel"><div class="panel-header"><h3>${esc(t("returns"))}</h3><span class="panel-small">${cases.length} ${esc(t("records"))}</span>${button("refresh-returns",t("refresh"),"refresh",view.returnsLoading || view.offline)}</div>${view.returnsLoading ? skeleton(3) : cases.length ? `<div class="return-case-list">${cases.map(item).join("")}</div>` : empty(t("returnEmpty"),t("returnHelp"))}</div></div>`;
}
function settings() {
 return `<div class="screen">${title('SYSTEM / PREFERENCES',t('settings'),t('preferences'))}<div class="settings-grid"><div class="detail-block"><h3>${esc(t('session'))}</h3>${kv('SAFAR CONTROL','v0.3.2 · PWA')}${kv(t('accessType'),esc(demo ? 'DEMO' : t('verified')))}${kv(t('scope'),esc(currentScope() || '—'))}${kv(t('expires'),demo ? '—' : esc(dateStr(view.expiresAt)))}${scopeControl()}${!demo ? `<div class="divider"></div>${button('logout',t('logout'),'logout')}` : ''}</div><div class="detail-block"><h3>${esc(t('locale'))}</h3><div class="language-options"><button type="button" class="filter-chip ${language === 'uk' ? 'active' : ''}" data-language="uk" aria-pressed="${language === 'uk'}" lang="uk">Українська</button><button type="button" class="filter-chip ${language === 'ru' ? 'active' : ''}" data-language="ru" aria-pressed="${language === 'ru'}" lang="ru">Русский</button></div><div class="divider"></div><h3>${esc(t('install'))}</h3><p class="muted-text">${esc(t('installHelp'))}</p>${view.installPrompt ? button('install',t('install'),'download',false,'button-primary') : ''}</div><div class="detail-block"><h3>${esc(t('diagnostics'))}</h3>${kv(t('status'),esc(connectionLabel()))}${kv(t('lastSync'),esc(dateStr(view.lastSync)))}${button('refresh',t('refresh'),'refresh',view.offline || view.listLoading)}</div><div class="detail-block privacy-card">${icon('shield')}<h3>PRIVATE BY DESIGN</h3><p class="muted-text">${esc(t('privacy'))}</p></div></div></div>`;
}
function locked() {
 const pair = view.pair;
 return `<main class="locked" id="mainContent"><div class="locked-card"><div class="locked-symbol">SAFAR</div><div class="eyebrow">SAFAR CONTROL / RELEASE 03.2</div><div class="lock-badge">${icon('shield')}</div><h1>${esc(t(pair ? 'pairing' : 'privateSpace'))}</h1><p>${esc(t(pair ? 'pairInstruction' : 'loginHelp'))}</p>${view.loading ? '<div class="loading-bar"><span></span></div>' : ''}${pair ? `<div class="pairing-code"><code>/app ${esc(pair.code)}</code></div><div class="pairing-meta">${esc(t('pairExpires'))}: ${esc(dateStr(pair.expires_at))}</div><div class="pairing-wait" role="status">${icon('clock')}${esc(t('pairWaiting'))}</div>` : ''}${view.offline ? `<div class="offline-banner" role="status">${icon('wifi')}<span>${esc(t('offline'))}. ${esc(t('privacy'))}</span></div>` : ''}${view.offline ? button('reconnect',t('refresh'),'wifi',view.loading,'button-quiet') : ''}${view.pairError ? `<div class="form-error" role="alert">${esc(view.pairError)}</div>` : ''}<div class="locked-actions">${button(pair ? 'pair-check' : 'pair-start',t(view.pairBusy ? 'loading' : pair ? 'pairCheck' : 'pair'),'lock',view.loading || view.pairBusy || view.offline,'button-primary')}${pair ? button('pair-cancel',t('cancel'),'close') : ''}<a href="/safar/?demo=1" class="button-quiet demo-link">${esc(t('demoButton'))}${icon('arrow')}</a></div><div class="locked-footer">SAFAR CONTROL 03.2 · ${esc(t('secured'))}</div><div class="language-options"><button type="button" data-language="uk" aria-pressed="${language === 'uk'}">UK</button><span>/</span><button type="button" data-language="ru" aria-pressed="${language === 'ru'}">RU</button></div></div></main>`;
}
function render(options={}) {
 const focused = document.activeElement;
 const preserve = options.preserve !== false && focused && app.contains(focused) && focused.id;
 let start, end;
 if (preserve) { try { start = focused.selectionStart; end = focused.selectionEnd; } catch (_) {} }
 document.documentElement.lang = language;
 app.className = 'safar-shell';
 if (!view.authed && !demo) app.innerHTML = locked();
 else {
 const content = view.selected ? detail() : view.tab === 'home' ? home() : view.tab === 'orders' ? orderList() : view.tab === 'shipments' ? orderList(true) : view.tab === 'returns' ? returnScreen() : view.tab === 'senders' ? senders() : settings();
 app.innerHTML = `${sidebar()}<main class="workspace" id="mainContent">${topbar()}${demo ? `<div class="demo-banner"><strong>DEMO</strong><span>${esc(t('demoNotice'))}</span></div>` : ''}${view.offline ? `<div id="offlineBanner" class="offline-banner" role="status">${icon('wifi')}<div><strong>${esc(t('offline'))}</strong><span>${esc(t('offlineHint'))}</span></div></div>` : ''}${view.scopes.length > 1 && view.tab !== 'settings' && !view.selected ? `<div class="scope-strip">${scopeControl()}</div>` : ''}${content}<footer class="footer-note"><span>SAFAR / CONTROL 03.2</span><span>${esc(t('lastSync'))} ${esc(dateStr(view.lastSync))}</span></footer></main>${bottom()}`;
 }
 if (preserve) { const target = document.getElementById(focused.id); if (target) { target.focus({preventScroll:true}); if (typeof start === 'number') { try { target.setSelectionRange(start,end); } catch (_) {} } } }
}
function toast(message) { const region = document.getElementById('toastRegion'); if (!region) return; region.textContent = message; region.classList.add('visible'); clearTimeout(toast.timer); toast.timer = setTimeout(() => { region.classList.remove('visible'); region.textContent = ''; },4500); }
function messageFor(status) { return t(status === 401 ? 'expired' : status === 403 ? 'denied' : status === 404 ? 'notFound' : status === 409 ? 'conflict' : status === 400 || status === 422 ? 'validation' : status === 429 ? 'rateLimit' : 'networkError'); }
function clearSession(message='') {
 sessionEpoch++; pairSequence++; clearTimeout(pairingTimer); clearTimeout(searchTimer); clearTimeout(expiryTimer); view.saving = false;
 view.listLoading = false; view.moreLoading = false; view.detailLoading = false; view.trackingLoading = false; view.sendersLoading = false; view.analyticsLoading = false; view.error = ''; view.detailError = ''; view.trackingError = ''; view.sendersError = ''; view.analyticsError = '';
 view.authed = false; view.isAdmin = false; view.archiveView = 'active'; view.carrierEvents = {}; view.autoIntakeEnabled = false; view.mediaIntakeEnabled = false; view.ocrEnabled = false; view.returnCases = []; view.returnTracking = {}; view.returnTrackingErrors = {}; view.returnTrackingBusy = {}; view.returnsError = ''; view.csrf = ''; view.expiresAt = null; view.orders = []; view.counts = {}; view.detailCache = {}; view.tracking = {}; view.radarErrors = {}; view.radarLoading = false; view.selected = null; view.editDraft = null; view.editing = false; view.senders = null; view.analytics = null; view.lastSync = null; view.scopes = []; view.chatId = null; view.loading = false; view.pair = null; view.pairBusy = false; view.pairError = message; closeDialog(); for (const dialog of document.querySelectorAll('dialog')) { dialog.replaceChildren(); dialog.correction = null; } view.photoErrors.clear(); view.photoAttempts = {}; listController?.abort(); requestSequence++; render();
}
async function jsonRequest(url,options={}) {
 if (!navigator.onLine || view.offline) { const error = new Error(t('networkError')); error.status = 0; throw error; }
 const headers = {...options.headers};
 if (options.body !== undefined && !(options.body instanceof FormData)) headers['Content-Type'] = 'application/json';
 if (options.method && !['GET','HEAD'].includes(options.method) && view.csrf) headers['X-CSRF-Token'] = view.csrf;
 let response;
 try { response = await fetch(url,{credentials:'same-origin',cache:'no-store',redirect:'error',...options,headers}); }
 catch (error) { if (error.name === 'AbortError') throw error; const safeError = new Error(t('networkError')); safeError.status = 0; throw safeError; }
 if (!response.ok) { if (response.status === 401 && view.authed) clearSession(t('expired')); const error = new Error(messageFor(response.status)); error.status = response.status; throw error; }
 return response.json();
}
function scopedURL(path,params={}) { const query = new URLSearchParams(params); if (view.chatId !== null) query.set('chat_id',String(view.chatId)); return path + (query.size ? '?' + query : ''); }
function dateWindow() {
 if (view.period === 'all') return {};
 const now = Date.now() / 1000;
 // Kyiv local midnight remains accurate across daylight-saving transitions.
 if (view.period === 'today') { const parts = new Intl.DateTimeFormat('en-CA',{timeZone:'Europe/Kyiv',year:'numeric',month:'2-digit',day:'2-digit'}).formatToParts(new Date()); const p = Object.fromEntries(parts.map(x => [x.type,x.value])); const noon = new Date(`${p.year}-${p.month}-${p.day}T12:00:00Z`); const hour = Number(new Intl.DateTimeFormat('en-GB',{timeZone:'Europe/Kyiv',hour:'2-digit',hourCycle:'h23'}).format(noon)); return {date_from:Math.round(Date.UTC(Number(p.year),Number(p.month)-1,Number(p.day)) / 1000 - (hour - 12) * 3600),date_to:Math.round(now)}; }
 return {date_from:Math.round(now - Number(view.period) * 86400),date_to:Math.round(now)};
}
function demoList(offset,limit) {
 const window = dateWindow();
 let items = demoOrders.filter(o => (view.tab !== 'shipments' || o.ttn) && (view.filter === 'all' || (view.filter === 'attention' ? ['invalid','failed','uncertain'].includes(o.state) : o.state === view.filter)) && (!view.sender || o.sender_profile === view.sender) && (!window.date_from || o.updated_at >= window.date_from) && [o.recipient,o.city,o.ttn,o.warehouse].join(' ').toLocaleLowerCase().includes(view.search.toLocaleLowerCase()));
 items = items.sort((a,b) => view.sort === 'updated_asc' ? a.updated_at - b.updated_at : b.updated_at - a.updated_at);
 return {orders:items.slice(offset,offset + limit),counts:demoOrders.reduce((c,o) => { c[o.state] = (c[o.state] || 0) + 1; return c; },{}),pagination:{total:items.length,offset,limit,next_offset:offset + limit < items.length ? offset + limit : null,has_more:offset + limit < items.length}};
}
async function fetchOrders(append=false) {
 if (view.offline && !demo) { view.error = t('networkError'); render(); return; }
 const sequence = ++requestSequence;
 listController?.abort(); listController = new AbortController();
 const offset = append ? Number(view.pagination.next_offset) || view.orders.length : 0;
 if (append) view.moreLoading = true; else { view.listLoading = true; view.error = ''; }
 render();
 try {
 const params = {limit:'20',offset:String(offset),sort:view.sort,...dateWindow()};
 if (view.search) params.search = view.search;
 if (view.filter !== 'all') params.status = view.filter;
 if (view.sender) params.sender_profile = view.sender;
 if (view.tab === 'shipments') params.has_ttn = 'true';
 if (view.archiveView === 'archived') params.archived = 'archived';
 const data = demo ? demoList(offset,20) : await jsonRequest(scopedURL('/api/safar/orders',params),{signal:listController.signal});
 if (sequence !== requestSequence) return;
 const rows = Array.isArray(data.orders) ? data.orders : [];
 view.orders = append ? [...view.orders,...rows.filter(o => !view.orders.some(old => old.id === o.id))] : rows;
 view.counts = data.counts || {}; view.pagination = data.pagination || {total:rows.length,has_more:false}; view.error = ''; view.lastSync = Date.now();
 } catch (error) { if (error.name !== 'AbortError' && sequence === requestSequence) view.error = error.message; }
 finally { if (sequence === requestSequence) { view.listLoading = false; view.moreLoading = false; if (['home','orders','shipments'].includes(view.tab) && !view.selected) render(); } }
}
async function fetchCarrierEvents(id) {
 const epoch=sessionEpoch,chat=view.chatId;
 if(demo || view.offline) return;
 try {
  const data=await jsonRequest(scopedURL('/api/safar/orders/'+encodeURIComponent(id)+'/carrier-events'));
  if(epoch===sessionEpoch && chat===view.chatId && view.selected===id) {
   view.carrierEvents[id]=data;
   render();
  }
 } catch (_) { /* Historical migration may not yet exist; live tracking remains available. */ }
}
async function fetchDetail(id) {
 const epoch = sessionEpoch;
 view.detailLoading = true; view.detailError = ''; render();
 try { const data = demo ? {order:demoOrders.find(o => o.id === id)} : await jsonRequest(scopedURL('/api/safar/orders/' + encodeURIComponent(id))); if (epoch === sessionEpoch && view.selected === id) { view.detailCache[id] = data.order; view.detailError = ''; if(data.order?.ttn) fetchCarrierEvents(id); } }
 catch (error) { if (epoch === sessionEpoch && view.selected === id) view.detailError = error.message; }
 finally { if (epoch === sessionEpoch && view.selected === id) { view.detailLoading = false; render(); } }
}
async function fetchSenders() {
 const epoch = sessionEpoch;
 view.sendersLoading = true; view.sendersError = ''; if (view.tab === 'senders') render();
 try { const result = demo ? {profiles:[{id:'default',label:'Основний кабінет НП',configured:true,primary:true,selected:true},{id:'fop',label:'Кабінет ФОП',configured:false}],selected_profile:'default',primary_profile:'default'} : await jsonRequest(scopedURL('/api/safar/senders')); if (epoch === sessionEpoch) view.senders = result; }
 catch (error) { if (epoch === sessionEpoch) view.sendersError = error.message; }
 finally { if (epoch === sessionEpoch) { view.sendersLoading = false; if ((view.authed || demo) && ['home','orders','shipments','senders'].includes(view.tab) && !view.selected) render(); } }
}
async function fetchAnalytics() {
 const epoch = sessionEpoch;
 view.analyticsLoading = true; view.analyticsError = ''; if (view.tab === 'home') render();
 try {
 if (demo) { const daily = Array.from({length:7},(_,i) => { const day = new Date(Date.now() - (6-i) * 86400000).toLocaleDateString('en-CA',{timeZone:'Europe/Kyiv'}); return {date:day,count:i === 6 ? demoOrders.length : 0}; }); view.analytics = {daily,with_ttn:demoOrders.filter(o => o.ttn).length}; }
 else { const data = await jsonRequest(scopedURL('/api/safar/analytics')); if (epoch === sessionEpoch) view.analytics = data.analytics || data; }
 } catch (error) { if (epoch === sessionEpoch) view.analyticsError = error.message; }
 finally { if (epoch === sessionEpoch) { view.analyticsLoading = false; if ((view.authed || demo) && view.tab === 'home' && !view.selected) render(); } }
}
function applySession(data) { view.isAdmin = data.is_admin === true; view.autoIntakeEnabled = data.auto_intake_enabled === true; view.mediaIntakeEnabled = data.media_intake_enabled === true; view.ocrEnabled = data.ocr_enabled === true; view.authed = true; view.csrf = data.csrf_token || ''; view.expiresAt = data.expires_at; view.chatId = data.chat_id ?? null; view.loading = false; view.pair = null; view.pairError = ''; clearTimeout(pairingTimer); clearTimeout(expiryTimer); const remaining = Number(data.expires_at) * 1000 - Date.now(); if (Number.isFinite(remaining)) expiryTimer = setTimeout(() => clearSession(t('expired')),Math.max(0,remaining)); }
async function loadWorkspace() {
 const epoch = sessionEpoch;
 if (!demo) { try { const data = await jsonRequest('/api/safar/scopes'); if (epoch !== sessionEpoch) return; view.scopes = data.scopes || []; view.chatId = data.selected_chat_id ?? view.chatId; } catch (error) { if (epoch !== sessionEpoch || !view.authed) return; } }
 else { view.chatId = null; }
 await Promise.allSettled([fetchOrders(),fetchSenders(),fetchAnalytics()]);
}
async function boot() {
 if (demo) { view.loading = false; await loadWorkspace(); return; }
 try { const data = await jsonRequest('/api/safar/session'); applySession(data); }
 catch (_) {
 const tg = window.Telegram?.WebApp;
 if (tg?.initData) { try { tg.ready(); tg.expand(); const data = await jsonRequest('/api/safar/session',{method:'POST',body:JSON.stringify({initData:tg.initData})}); applySession(data); } catch (error) { view.pairError = error.message; } }
 }
 view.loading = false; render();
 if (view.authed) await loadWorkspace();
}
async function startPairing() {
 view.pairBusy = true; view.pairError = ''; render();
 try { const data = await jsonRequest('/api/safar/pairing/start',{method:'POST',body:'{}'}); view.pair = {device_token:data.device_token,code:data.code,expires_at:data.expires_at,poll_after:Math.max(3,Number(data.poll_after) || 3)}; pairSequence++; schedulePairing(); }
 catch (error) { view.pairError = error.message; }
 finally { view.pairBusy = false; render(); }
}
function schedulePairing() { clearTimeout(pairingTimer); if (view.pair && !view.authed && !document.hidden) pairingTimer = setTimeout(checkPairing,view.pair.poll_after * 1000); }
async function checkPairing() {
 const pair = view.pair, sequence = pairSequence;
 if (!pair || view.pairBusy) return;
 if (timestamp(pair.expires_at)?.getTime() <= Date.now()) { view.pair = null; view.pairError = t('pairExpired'); render(); return; }
 if (view.offline) { schedulePairing(); return; }
 view.pairBusy = true;
 try { const data = await jsonRequest('/api/safar/pairing/complete',{method:'POST',body:JSON.stringify({device_token:pair.device_token})}); if (sequence !== pairSequence) return; if (data.ok && data.csrf_token) { applySession(data); render(); await loadWorkspace(); } else schedulePairing(); }
 catch (error) { if (sequence === pairSequence) { view.pairError = error.message; if ([400,401,403,404,410].includes(error.status)) view.pair = null; else schedulePairing(); } }
 finally { if (sequence === pairSequence) { view.pairBusy = false; render(); } }
}
function initEdit(order) { view.editing = true; view.editError = ''; view.editDraft = Object.fromEntries(fieldDefs.map(([key,publicKey]) => [key,String(order.pending_order?.[key] ?? order[publicKey] ?? '')])); render(); document.getElementById('edit-full_name')?.focus({preventScroll:true}); }
function reviewCorrection() {
 const form = document.getElementById('correctionForm'), order = view.detailCache[view.selected];
 if (!form || !order) return;
 const inputs = Object.fromEntries(new FormData(form));
 view.editDraft = inputs;
 const changes = {};
 for (const [key,publicKey] of fieldDefs) { const val = String(inputs[key] || '').trim(); if (val !== String(order[publicKey] ?? '').trim()) changes[key] = val; }
 if (!Object.keys(changes).length) { view.editError = t('noChanges'); render(); return; }
 const numberKeys = ['cost','cod_amount','weight'];
 if (Object.entries(changes).some(([key,val]) => !val || (numberKeys.includes(key) && (!/^\d{1,8}(?:[.,]\d{1,2})?$/.test(val.replace(/\s/g,'')) || Number(val.replace(',','.')) < (key === 'cod_amount' ? 0 : 0.01))) || (key === 'warehouse' && !/^[1-9]\d{0,4}$/.test(val)) || (key === 'full_name' && !/^[A-Za-zА-Яа-яІіЇїЄєҐґ'’\-]+(?:\s+[A-Za-zА-Яа-яІіЇїЄєҐґ'’\-]+){1,3}$/.test(val)) || (key === 'phone' && !/^\+?\d[\d\s()-]{8,18}$/.test(val)))) { view.editError = t('validation'); render(); return; }
 view.editError = '';
 const dialog = document.getElementById('actionDialog');
 dialog.innerHTML = `<div class="dialog-top"><div><div class="eyebrow">SAFE CORRECTION</div><h2 id="actionTitle">${esc(t('changes'))}</h2></div>${button('close-dialog',t('close'),'close',false,'icon-button',`aria-label="${esc(t('close'))}"`)}</div><div class="review-diff">${Object.entries(changes).map(([key,val]) => { const def = fieldDefs.find(f => f[0] === key); return `<div><strong>${esc(t(def[3]))}</strong><div><span>${esc(t('current'))}</span><p>${esc(order[def[1]] ?? '—')}</p></div><div class="proposed"><span>${esc(t('proposed'))}</span><p>${esc(val)}</p></div></div>`; }).join('')}</div><div class="notice">${icon('shield')}<span>${esc(t('correctionHelp'))}</span></div><div id="correctionError" class="form-error" role="alert"></div><div class="form-actions">${button('confirm-correction',t('save'),'shield',false,'button-primary')}${button('close-dialog',t('cancel'),'close')}</div>`;
 dialog.correction = {id:order.id,revision:order.revision,fields:changes}; openDialog(dialog);
}
async function saveCorrection() {
 const epoch = sessionEpoch;
 const dialog = document.getElementById('actionDialog'), correction = dialog.correction;
 if (!correction || view.saving) return;
 view.saving = true;
 for (const button of dialog.querySelectorAll('button')) button.disabled = true;
 try {
 const data = demo ? (() => { const order = demoOrders.find(o => o.id === correction.id); order.pending_order = {...(order.pending_order || {}),...correction.fields}; order.updated_at = Date.now() / 1000; order.revision = 'demo-' + Date.now(); return {order,staged:true}; })() : await jsonRequest('/api/safar/orders/' + encodeURIComponent(correction.id) + '/corrections',{method:'POST',body:JSON.stringify({fields:correction.fields,expected_revision:correction.revision,chat_id:view.chatId})});
 if (epoch !== sessionEpoch) return;
 view.detailCache[correction.id] = data.order; view.orders = view.orders.map(o => o.id === correction.id ? data.order : o); view.editing = false; view.editDraft = null; view.saving = false; closeDialog(); render(); toast(t('saved'));
 } catch (error) { const region = document.getElementById('correctionError'); if (region) region.textContent = error.message; if (error.status === 409) { view.detailError = error.message; } }
 finally { view.saving = false; for (const button of dialog.querySelectorAll('button')) button.disabled = false; }
}
function openReturnDialog() {
 const dialog = document.getElementById("actionDialog");
 const reasonOptions = [["refused_by_recipient",language==="ru"?"Отказ получателя":"Відмова одержувача"],["unclaimed",language==="ru"?"Не забрали":"Не забрали"],["easy_return_after_delivery",language==="ru"?"Лёгкий возврат":"Легке повернення"],["customer_exchange",language==="ru"?"Обмен":"Обмін"],["other",language==="ru"?"Другое":"Інше"]];
 dialog.innerHTML = `<div class="dialog-top"><div><div class="eyebrow">SAFAR / RETURN CONTROL</div><h2 id="actionTitle">${esc(t("returnNew"))}</h2></div>${button("close-dialog",t("close"),"close")}</div><p class="muted-text">${esc(t("returnHelp"))}</p><form id="returnForm"><label for="returnReason">${esc(t("returnReason"))}</label><select id="returnReason" name="reason">${reasonOptions.map(([key,label])=>`<option value="${key}">${esc(label)}</option>`).join("")}</select><div id="returnError" class="form-error" role="alert"></div><div class="form-actions"><button class="button-primary" type="submit">${icon("refresh")}${esc(t("returnNew"))}</button>${button("close-dialog",t("cancel"),"close")}</div></form>`;
 openDialog(dialog);
}
async function createReturnCase() {
 if (view.saving || demo || !view.selected) return;
 const dialog=document.getElementById("actionDialog"), errorBox=document.getElementById("returnError");
 const reason=document.getElementById("returnReason")?.value;
 const epoch=sessionEpoch;
 view.saving=true; for(const button of dialog.querySelectorAll("button")) button.disabled=true;
 try {
  const data=await jsonRequest("/api/safar/returns",{method:"POST",body:JSON.stringify({order_id:view.selected,reason,chat_id:view.chatId})});
  if (epoch !== sessionEpoch) return;
  view.saving=false; closeDialog(); view.selected=null; view.tab="returns"; toast(t("returnSaved")); await fetchReturns();
 } catch(error){if(epoch===sessionEpoch && errorBox) errorBox.textContent=error.message;}
 finally {view.saving=false; for(const button of dialog.querySelectorAll("button")) button.disabled=false;}
}
function openIntake() {
 const dialog = document.getElementById("actionDialog");
 if (!window.crypto?.randomUUID) { toast(t("networkError")); return; }
 dialog.intakeRequestId = window.crypto.randomUUID().replace(/-/g,"");
 const available = !demo && view.authed && view.autoIntakeEnabled && !view.offline;
 dialog.innerHTML = `<div class="dialog-top"><div><div class="eyebrow">SAFAR / SMART INTAKE</div><h2 id="actionTitle">${esc(t("intakeTitle"))}</h2></div>${button("close-dialog",t("close"),"close",false,"icon-button")}</div><p class="muted-text">${esc(t("intakeHelp"))}</p><form id="intakeForm" autocomplete="off"><label for="intakeText">${esc(t("intakeSource"))}</label><textarea id="intakeText" name="source" rows="9" maxlength="8000" placeholder="${esc(t("intakeSource"))}" ${available ? "" : "disabled"}></textarea><p class="muted-text intake-hint">${view.mediaIntakeEnabled ? esc(language==="ru"?"Фото сохраняется в вашем личном чате Telegram-бота SAFAR. Максимум 2 МБ (JPG/PNG). Для скриншота без текста требуется включённый OCR.":"Фото зберігається у вашому особистому чаті Telegram-бота SAFAR. До 2 МБ (JPG/PNG). Для скриншота без тексту потрібен увімкнений OCR.") : esc(t("intakeImagesLater"))}</p>${view.mediaIntakeEnabled ? `<label for="intakeImage">${esc(language==="ru"?"Фото товара или скриншот (необязательно)":"Фото товару або скриншот (необов’язково)")}</label><input id="intakeImage" type="file" name="image" accept="image/jpeg,image/png" ${available?"":"disabled"}>`:""}${available ? "" : `<div class="notice">${icon("shield")}${esc(demo ? t("demoNotice") : t("intakeDisabled"))}</div>`}<div id="intakeError" class="form-error" role="alert"></div><div class="form-actions"><button type="submit" class="button-primary" ${available ? "" : "disabled"}>${icon("orders")}${esc(t("intakeSubmit"))}</button>${button("close-dialog",t("cancel"),"close")}</div></form>`;
 openDialog(dialog); document.getElementById("intakeText")?.focus({preventScroll:true});
}
async function submitIntake() {
 const dialog=document.getElementById("actionDialog"), input=document.getElementById("intakeText");
 const errorBox=document.getElementById("intakeError"), rawText=input?.value || "";
 const image=document.getElementById("intakeImage")?.files?.[0];
 if (!rawText.trim() && !image) { if(errorBox) errorBox.textContent=t("intakeEmpty"); return; }
 if (image && (!view.mediaIntakeEnabled || !["image/png","image/jpeg"].includes(image.type) || image.size>2*1024*1024)) {
  if(errorBox) errorBox.textContent=t("validation"); return;
 }
 if(image && !rawText.trim() && !view.ocrEnabled) {
  if(errorBox) errorBox.textContent=language==="ru"?"Для фото без подписи требуется OCR. Добавьте текст заказа.":"Для фото без підпису потрібен OCR. Додайте текст замовлення.";return;
 }
 if (view.saving || !view.autoIntakeEnabled || demo || view.offline) return;
 const epoch=sessionEpoch;
 view.saving=true; for(const el of dialog.querySelectorAll("button")) el.disabled=true;
 try {
  const body=image?new FormData():null;
  if(body) {body.set("text",rawText);body.set("request_id",dialog.intakeRequestId);body.set("chat_id",String(view.chatId));body.set("image",image,image.name);}
  const data=await jsonRequest(image?"/api/safar/orders/intake/photo":"/api/safar/orders/intake",
   {method:"POST",body:body||JSON.stringify({text:rawText,request_id:dialog.intakeRequestId,chat_id:view.chatId})});
  if(epoch!==sessionEpoch) return;
  view.saving=false; closeDialog(); toast(t(data.accepted ? "intakeQueued" : "intakeExisting"));
  view.selected=null; view.tab="orders"; view.search=""; view.filter="all"; view.orders=[];
  await fetchOrders();
 } catch(error) { if(epoch===sessionEpoch && errorBox) errorBox.textContent=error.message; }
 finally { view.saving=false; for(const el of dialog.querySelectorAll("button")) el.disabled=false; }
}
function openEasyReturnLink(id) {
 const c=view.returnCases.find(x=>x.id===id);
 if (!c || c.reason!=="easy_return_after_delivery" || c.reverse_ttn) return;
 const dialog=document.getElementById("actionDialog");
 dialog.linkCaseId=id;
 const heading=language==="ru"?"Связать обратную ТТН":"Пов’язати зворотну ТТН";
 const hint=language==="ru"?"Номер будет привязан только при подтверждении связи исходной ТТН через API Новой Почты. Никакой новой отправки не создаётся.":"Номер буде пов’язано лише після підтвердження зв’язку через API Нової пошти. Нове відправлення не створюється.";
 dialog.innerHTML=`<div class="dialog-top"><div><div class="eyebrow">EASY RETURN / VERIFIED</div><h2 id="actionTitle">${esc(heading)}</h2></div>${button("close-dialog",t("close"),"close",false,"icon-button")}</div><p class="muted-text">${esc(hint)}</p><form id="easyReturnLinkForm"><label for="reverseTtn">${esc(t("waybill"))}</label><input id="reverseTtn" name="reverse_ttn" inputmode="numeric" autocomplete="off" maxlength="14" pattern="[0-9]{14}" required placeholder="20400000000000"><div id="easyReturnLinkError" role="alert" class="form-error"></div><div class="form-actions"><button class="button-primary" type="submit">${icon("check")}${esc(heading)}</button>${button("close-dialog",t("cancel"),"close")}</div></form>`;
 openDialog(dialog); document.getElementById("reverseTtn")?.focus({preventScroll:true});
}
async function submitEasyReturnLink() {
 const dialog=document.getElementById("actionDialog"), id=dialog.linkCaseId;
 const input=document.getElementById("reverseTtn"), err=document.getElementById("easyReturnLinkError");
 if(!id || !input || !/^[0-9]{14}$/.test(input.value)) {if(err)err.textContent=t("validation");return;}
 const epoch=sessionEpoch; view.saving=true;
 for(const control of dialog.querySelectorAll("button")) control.disabled=true;
 try {
  const result=await jsonRequest("/api/safar/returns/"+encodeURIComponent(id)+"/link-easy-return",{method:"POST",body:JSON.stringify({reverse_ttn:input.value,chat_id:view.chatId})});
  if(epoch!==sessionEpoch) return;
  view.saving=false; view.returnCases=view.returnCases.map(c=>c.id===id?result.case:c);
  closeDialog(); render(); toast(language==="ru"?"Связь подтверждена Новой Почтой":"Зв’язок підтверджено Новою поштою");
 } catch(error) {if(epoch===sessionEpoch&&err)err.textContent=error.message;}
 finally {view.saving=false;for(const control of dialog.querySelectorAll("button"))control.disabled=false;}
}
function openWarehouseReceipt(id) {
 const c=view.returnCases.find(x=>x.id===id);
 if(!c || c.warehouse_state==="received" || demo || view.offline) return;
 const dialog=document.getElementById("actionDialog");
 dialog.warehouseCaseId=id;
 const titleText=language==="ru"?"Подтвердить физическое получение":"Підтвердити фактичне отримання";
 const hint=language==="ru"?"Только после фактической приёмки посылки на вашем складе. Впишите номер исходной или проверенной обратной ТТН. Действие будет записано в журнале, финансы не изменятся.":"Тільки після фактичного приймання посилки на складі. Введіть номер початкової або підтвердженої зворотної ТТН. Дія буде записана в журналі, фінанси не зміняться.";
 const checkedLabel=language==="ru"?"Подтверждаю, что посылка физически получена":"Підтверджую фактичне отримання посилки";
 dialog.innerHTML=`<div class="dialog-top"><div><div class="eyebrow">SAFAR / WAREHOUSE AUDIT</div><h2 id="actionTitle">${esc(titleText)}</h2></div>${button("close-dialog",t("close"),"close",false,"icon-button")}</div><p class="muted-text">${esc(hint)}</p><form id="warehouseReceiptForm"><label for="warehouseTtn">${esc(t("waybill"))} · ${esc(c.outbound_ttn)}${c.reverse_ttn?` / ${esc(c.reverse_ttn)}`:""}</label><input id="warehouseTtn" inputmode="numeric" required autocomplete="off" maxlength="14" pattern="[0-9]{14}" placeholder="20400000000000"><label class="warehouse-check"><input id="warehouseAcknowledged" type="checkbox" required><span>${esc(checkedLabel)}</span></label><div class="form-error" role="alert" id="warehouseReceiptError"></div><div class="form-actions"><button type="submit" class="button-primary">${icon("check")}${esc(titleText)}</button>${button("close-dialog",t("cancel"),"close")}</div></form>`;
 openDialog(dialog); document.getElementById("warehouseTtn")?.focus({preventScroll:true});
}
async function submitWarehouseReceipt() {
 const dialog=document.getElementById("actionDialog"),id=dialog.warehouseCaseId;
 const ttn=document.getElementById("warehouseTtn")?.value || "";
 const acknowledged=document.getElementById("warehouseAcknowledged")?.checked === true;
 const error=document.getElementById("warehouseReceiptError");
 if(!id || !/^[0-9]{14}$/.test(ttn) || !acknowledged) {if(error)error.textContent=t("validation");return;}
 const epoch=sessionEpoch; view.saving=true;
 for(const b of dialog.querySelectorAll("button")) b.disabled=true;
 try {
  const result=await jsonRequest("/api/safar/returns/"+encodeURIComponent(id)+"/warehouse-receipt",{method:"POST",body:JSON.stringify({chat_id:view.chatId,ttn,physically_received:true})});
  if(epoch!==sessionEpoch) return;
  view.saving=false;view.returnCases=view.returnCases.map(c=>c.id===id?result.case:c);
  closeDialog();render();toast(language==="ru"?"Получение записано в журнал":"Отримання записано до журналу");
 } catch(e) {if(epoch===sessionEpoch && error)error.textContent=e.message;}
 finally {view.saving=false;for(const b of dialog.querySelectorAll("button"))b.disabled=false;}
}
function openArchiveDialog() {
 const order=view.detailCache[view.selected];
 if(!order || !view.isAdmin || demo || view.offline || !order.can_archive || view.saving) return;
 const archived=!order.archived, ru=language==='ru';
 const titleText=archived?(ru?'Архивировать заказ?':'Архівувати замовлення?'):(ru?'Восстановить заказ?':'Відновити замовлення?');
 const help=ru?'Это действие меняет только видимость записи в SAFAR. ТТН в Новой Почте не удаляется и не отменяется. История и фотографии сохраняются.':'Ця дія змінює лише видимість запису в SAFAR. ТТН у Новій пошті не видаляється та не скасовується. Історія і фото зберігаються.';
 const ack=ru?'Я подтверждаю действие только с записью SAFAR':'Підтверджую дію лише із записом SAFAR';
 const dialog=document.getElementById('actionDialog');
 dialog.archiveTarget={id:order.id,archived,revision:order.revision};
 dialog.innerHTML=`<div class="dialog-top"><div><div class="eyebrow">ADMIN / LOCAL ARCHIVE</div><h2 id="actionTitle">${esc(titleText)}</h2></div>${button('close-dialog',t('close'),'close',false,'icon-button')}</div><div class="archive-order-summary"><strong>${esc(order.recipient)}</strong><span>${esc(order.ttn||t('noTtn'))}</span></div><p class="muted-text">${esc(help)}</p><form id="archiveOrderForm"><label class="warehouse-check"><input type="checkbox" id="archiveConfirmation" required><span>${esc(ack)}</span></label><div id="archiveOrderError" class="form-error" role="alert"></div><div class="form-actions"><button type="submit" class="button-primary">${icon('check')}${esc(titleText)}</button>${button('close-dialog',t('cancel'),'close')}</div></form>`;
 openDialog(dialog);
}
async function submitArchiveOrder() {
 const dialog=document.getElementById('actionDialog'),target=dialog.archiveTarget;
 if(!target || !view.isAdmin || !document.getElementById('archiveConfirmation')?.checked || view.saving)return;
 const error=document.getElementById('archiveOrderError'),epoch=sessionEpoch;
 view.saving=true;for(const b of dialog.querySelectorAll('button'))b.disabled=true;
 try{
  const data=await jsonRequest(scopedURL('/api/safar/orders/'+encodeURIComponent(target.id)+'/archive'),{method:'POST',body:JSON.stringify({chat_id:view.chatId,archived:target.archived,expected_revision:target.revision,confirm_local_only:true})});
  if(epoch!==sessionEpoch)return;
  view.saving=false;view.detailCache[target.id]=data.order;
  view.orders=view.orders.map(o=>o.id===target.id?data.order:o);
  view.counts.archived=Math.max(0,Number(view.counts.archived||0)+(data.changed?(target.archived?1:-1):0));
  closeDialog();render();
  toast(target.archived?(language==='ru'?'Заказ перемещён в архив SAFAR':'Замовлення переміщено до архіву SAFAR'):(language==='ru'?'Заказ восстановлен':'Замовлення відновлено'));
 }catch(e){if(epoch===sessionEpoch&&error)error.textContent=e.message;}
 finally{view.saving=false;for(const b of dialog.querySelectorAll('button'))b.disabled=false;}
}
function openReturnExpense(id) {
 const c=view.returnCases.find(x=>x.id===id);if(!c||demo||view.offline||!window.crypto?.randomUUID)return;
 const dialog=document.getElementById("actionDialog");
 dialog.expenseCaseId=id;dialog.expenseRequestId=window.crypto.randomUUID().replace(/-/g,"");
 const titleText=language==="ru"?"Учесть расход по возврату":"Облік витрат на повернення";
 const help=language==="ru"?"Введите точную сумму по квитанции. Запись отражает сообщение оператора и не подтверждает банковское списание.":"Вкажіть точну суму за квитанцією. Запис відображає повідомлення оператора, але не підтверджує банківське списання.";
 const check=language==="ru"?"Подтверждаю сумму и наличие основания":"Підтверджую суму та наявність підтвердження";
 const categories=[["return_delivery",language==="ru"?"Обратная доставка":"Зворотна доставка"],["storage",language==="ru"?"Хранение":"Зберігання"],["other",language==="ru"?"Другие расходы":"Інші витрати"]];
 dialog.innerHTML=`<div class="dialog-top"><div><div class="eyebrow">SAFAR / COST AUDIT</div><h2 id="actionTitle">${esc(titleText)}</h2></div>${button("close-dialog",t("close"),"close",false,"icon-button")}</div><p class="muted-text">${esc(help)}</p><form id="returnExpenseForm"><label for="returnExpenseAmount">${esc(language==="ru"?"Сумма, грн":"Сума, грн")}</label><input id="returnExpenseAmount" required type="text" inputmode="decimal" maxlength="9" placeholder="120.50"><label for="returnExpenseCategory">${esc(language==="ru"?"Категория":"Категорія")}</label><select id="returnExpenseCategory">${categories.map(([key,label])=>`<option value="${key}">${esc(label)}</option>`).join("")}</select><label for="returnExpenseRef">${esc(language==="ru"?"Номер чека или основание":"Номер чека чи підстава")}</label><input id="returnExpenseRef" required maxlength="120" placeholder="Receipt №"><label class="warehouse-check"><input type="checkbox" required id="returnExpenseAck"><span>${esc(check)}</span></label><div id="returnExpenseError" class="form-error" role="alert"></div><div class="form-actions"><button class="button-primary" type="submit">${icon("check")}${esc(titleText)}</button>${button("close-dialog",t("cancel"),"close")}</div></form>`;
 openDialog(dialog);document.getElementById("returnExpenseAmount")?.focus({preventScroll:true});
}
async function submitReturnExpense() {
 const dialog=document.getElementById("actionDialog"),id=dialog.expenseCaseId;
 const error=document.getElementById("returnExpenseError");
 const amount=document.getElementById("returnExpenseAmount")?.value?.trim(), category=document.getElementById("returnExpenseCategory")?.value,reference=document.getElementById("returnExpenseRef")?.value,acknowledged=document.getElementById("returnExpenseAck")?.checked===true;
 if (!id || !/^(?:0|[1-9][0-9]{0,5})(?:\.[0-9]{1,2})?$/.test(amount||"") || !acknowledged || (reference||"").trim().length<3){if(error)error.textContent=t("validation");return;}
 if(view.saving||demo||view.offline)return;
 const epoch=sessionEpoch;view.saving=true;for(const b of dialog.querySelectorAll("button"))b.disabled=true;
 try {
  const result=await jsonRequest("/api/safar/returns/"+encodeURIComponent(id)+"/expense",{method:"POST",body:JSON.stringify({chat_id:view.chatId,amount,category,reference,request_id:dialog.expenseRequestId,acknowledged:true})});
  if(epoch!==sessionEpoch)return;
  view.saving=false;view.returnCases=view.returnCases.map(c=>c.id===id?result.case:c);closeDialog();render();toast(language==="ru"?"Расход сохранён с основанием":"Витрати збережено з підтвердженням");
 } catch(e){if(epoch===sessionEpoch&&error)error.textContent=e.message;}
 finally {view.saving=false;for(const b of dialog.querySelectorAll("button"))b.disabled=false;}
}
let dialogReturnFocus = null;
function openDialog(dialog) { dialogReturnFocus = document.activeElement; if (!dialog.open) dialog.showModal(); document.body.classList.add('dialog-open'); }
function closeDialog() { if (view.saving) return; for (const dialog of document.querySelectorAll('dialog[open]')) dialog.close(); view.photo = null; document.body.classList.remove('dialog-open'); if (dialogReturnFocus?.isConnected) dialogReturnFocus.focus({preventScroll:true}); dialogReturnFocus = null; }
function showPhoto(index) {
 const order = view.detailCache[view.selected]; if (!order || !(order.photos || []).length) return;
 const total = order.photos.length; view.photo = {id:order.id,index:(index + total) % total};
 const dialog = document.getElementById('photoDialog');
 dialog.innerHTML = `<div class="dialog-top"><h2 id="photoTitle">${esc(t('photo'))} ${view.photo.index + 1} / ${total}</h2>${button('close-dialog',t('close'),'close',false,'icon-button',`aria-label="${esc(t('close'))}"`)}</div><div class="lightbox-photo">${photoContent(order,view.photo.index,true)}</div><div class="lightbox-controls">${button('photo-prev',t('previous'),'back',total < 2,'icon-button',`aria-label="${esc(t('previous'))}"`)}<span>${view.photo.index + 1} / ${total}</span>${button('photo-next',t('next'),'arrow',total < 2,'icon-button',`aria-label="${esc(t('next'))}"`)}${!demo ? button('photo-retry',t('retryPhoto'),'refresh',view.offline) : ''}</div>`;
 if (!dialog.open) openDialog(dialog);
}
async function fetchTracking() {
 const epoch = sessionEpoch;
 const id = view.selected; if (!id || view.trackingLoading) return;
 view.trackingLoading = true; view.trackingError = ''; render();
 try { const data = demo ? {tracking:{status_text:language === 'ru' ? 'Демо: статус перевозчика не запрашивается' : 'Демо: статус перевізника не запитується',checked_at:Date.now() / 1000}} : await jsonRequest(scopedURL('/api/safar/orders/' + encodeURIComponent(id) + '/tracking')); if (epoch === sessionEpoch && view.selected === id) view.tracking[id] = data; }
 catch (error) { if (epoch === sessionEpoch && view.selected === id) view.trackingError = error.message; }
 finally { if (epoch === sessionEpoch) { view.trackingLoading = false; if (view.selected === id) render(); } }
}
async function fetchShipmentRadar() {
 if (view.tab !== "shipments" || view.radarLoading || view.offline) return;
 const epoch=sessionEpoch, chatId=view.chatId;
 const orders=view.orders.filter(o=>o.ttn && /^\d{14}$/.test(o.ttn)).slice(0,3);
 if(!orders.length) return;
 view.radarLoading=true; render();
 try {
  await Promise.allSettled(orders.map(async o=>{
   try {
    if (demo) return;
    const result=await jsonRequest(scopedURL("/api/safar/orders/" + encodeURIComponent(o.id) + "/tracking"));
    if(epoch===sessionEpoch && chatId===view.chatId) { view.tracking[o.id]=result; delete view.radarErrors[o.id]; }
   } catch(error) { if(epoch===sessionEpoch && chatId===view.chatId) view.radarErrors[o.id]=error.message; }
  }));
 } finally {if(epoch===sessionEpoch && chatId===view.chatId) {view.radarLoading=false; if(view.tab==="shipments" && !view.selected) render();}}
}
async function switchTab(tab) {
 if (!tabs.some(t => t[0] === tab)) return;
 const old = view.tab; view.tab = tab; view.selected = null; view.editing = false; view.editDraft = null; view.detailError = ''; closeDialog();
 if (old !== tab && ['home','orders','shipments'].includes(tab)) { view.filter = 'all'; view.search = ''; view.sender = ''; view.period = 'all'; view.sort = 'updated_desc'; view.orders = []; view.pagination = {}; }
 window.scrollTo({top:0,behavior:'instant'}); render({preserve:false}); document.querySelector('h1')?.focus({preventScroll:true});
 if (['home','orders','shipments'].includes(tab)) await fetchOrders();
 if (tab === 'shipments') await fetchShipmentRadar();
 if (tab === 'home') await fetchAnalytics();
 if (tab === 'senders') await fetchSenders();
 if (tab === 'returns') { await fetchReturns(); await refreshReturnTracking(); }
}
async function fetchReturns() {
 const epoch=sessionEpoch;
 view.returnsLoading=true; view.returnsError=''; render();
 try { const data=demo ? {cases:[]} : await jsonRequest(scopedURL('/api/safar/returns')); if (epoch === sessionEpoch) view.returnCases=data.cases || []; }
 catch (error) { if (epoch === sessionEpoch) view.returnsError=error.message; }
 finally { if (epoch === sessionEpoch) { view.returnsLoading=false; render(); } }
}
async function fetchReturnTracking(id) {
 if (!id || view.returnTrackingBusy[id]) return;
 const epoch=sessionEpoch;
 view.returnTrackingBusy[id]=true; delete view.returnTrackingErrors[id]; render();
 try {
  const data=await jsonRequest(scopedURL("/api/safar/returns/" + encodeURIComponent(id) + "/tracking"));
  if(epoch===sessionEpoch) view.returnTracking[id]=data.tracking;
 } catch(error) { if(epoch===sessionEpoch) view.returnTrackingErrors[id]=error.message; }
 finally { if(epoch===sessionEpoch) { delete view.returnTrackingBusy[id]; render(); } }
}
async function refreshReturnTracking() {
 if (view.offline || view.tab !== "returns" || demo) return;
 const now=Date.now()/1000;
 const cases=view.returnCases.filter(c=>!view.returnTrackingBusy[c.id]
   && now - (Number(view.returnTracking[c.id]?.checked_at)||0) > 180).slice(0,2);
 await Promise.allSettled(cases.map(c=>fetchReturnTracking(c.id)));
}
async function refreshCurrent() { if (view.selected) return fetchDetail(view.selected); if (view.tab === 'senders') return fetchSenders(); if (view.tab === 'returns') { await fetchReturns(); return refreshReturnTracking(); } if (view.tab === 'shipments') {await fetchOrders(); return fetchShipmentRadar();} const tasks = [fetchOrders()]; if (view.tab === 'home') tasks.push(fetchAnalytics()); await Promise.allSettled(tasks); }
async function logout() {
 try { await jsonRequest('/api/safar/logout',{method:'POST',body:'{}'}); sessionChannel?.postMessage('logout'); clearSession(); toast(t('loggedOut')); }
 catch (error) { toast(error.message); }
}
async function reconnect() {
 view.loading = true; view.pairError = ''; render();
 try {
  // A browser can report onLine while the shell came from an offline fallback.
  // Probe an uncached same-origin endpoint before enabling private operations.
  await fetch('/api/safar/session',{credentials:'same-origin',cache:'no-store',redirect:'error'});
  view.offline = false; await boot();
 } catch (_) { view.offline = true; view.loading = false; view.pairError = t('networkError'); render(); }
}
// Foreground-only refresh: this is not a 24/7 scheduler or background tracking service.
// Cap provider calls and rely on the server cache. Never store private data on disk.
setInterval(() => {
 if (demo || document.hidden || !navigator.onLine || view.offline || !view.authed
     || view.selected || view.saving || view.listLoading || view.returnsLoading) return;
 if (view.tab === "shipments") fetchShipmentRadar();
 else if (view.tab === "returns") fetchReturns().then(refreshReturnTracking);
 else if (view.tab === "home" || view.tab === "orders") fetchOrders();
}, 120000);
document.addEventListener('click',async event => {
 const control = event.target.closest('[data-tab],[data-filter],[data-order],[data-action],[data-language],[data-photo]');
 if (!control || control.disabled) return;
 if (control.dataset.tab) return switchTab(control.dataset.tab);
 if (control.dataset.language) { language = control.dataset.language === 'ru' ? 'ru' : 'uk'; try { localStorage.setItem('safar.locale',language); } catch (_) {} render({preserve:false}); return; }
 if (control.dataset.filter) { view.filter = control.dataset.filter; view.orders = []; return fetchOrders(); }
 if (control.dataset.order) { view.returnTab = view.tab === 'shipments' ? 'shipments' : 'orders'; view.selected = control.dataset.order; view.editing = false; view.editDraft = null; view.trackingError = ''; window.scrollTo({top:0,behavior:'instant'}); await fetchDetail(view.selected); document.querySelector('h1')?.focus({preventScroll:true}); return; }
 if (control.dataset.photo !== undefined) { showPhoto(Number(control.dataset.photo)); return; }
 const action = control.dataset.action;
 if (action === 'open-intake') return openIntake();
 if (action === 'show-active' || action === 'show-archive') {
  view.archiveView = action === 'show-archive' ? 'archived' : 'active';
  view.filter = 'all'; view.orders = []; view.pagination = {}; return fetchOrders();
 }
 if (action === 'open-archive') return openArchiveDialog();
 if (action === 'reconnect') return reconnect();
 if (action === 'refresh') return refreshCurrent();
 if (action === 'refresh-analytics') return fetchAnalytics();
 if (action === 'refresh-senders') return fetchSenders();
 if (action === 'radar-refresh') return fetchShipmentRadar();
 if (action === 'refresh-returns') return fetchReturns();
 if (action === 'track-return') return fetchReturnTracking(control.dataset.caseId);
 if (action === 'open-easy-link') return openEasyReturnLink(control.dataset.caseId);
 if (action === 'open-expense') return openReturnExpense(control.dataset.caseId);
 if (action === 'open-warehouse') return openWarehouseReceipt(control.dataset.caseId);
 if (action === 'open-return') return openReturnDialog();
 if (action === 'load-more') return fetchOrders(true);
 if (action === 'clear-filters') { view.search = ''; view.filter = 'all'; view.sender = ''; view.period = 'all'; return fetchOrders(); }
 if (action === 'detail-retry') return fetchDetail(view.selected);
 if (action === 'back') { view.selected = null; view.tab = view.returnTab; view.editing = false; view.editDraft = null; render({preserve:false}); document.querySelector('h1')?.focus({preventScroll:true}); return; }
 if (action === 'copy' && control.dataset.ttn) { try { await navigator.clipboard.writeText(control.dataset.ttn); toast(t('copied')); } catch (_) { toast(t('copyFailed')); } return; }
 if (action === 'track') return fetchTracking();
 if (action === 'edit') { const order = view.detailCache[view.selected]; if (order) initEdit(order); return; }
 if (action === 'cancel-edit') { view.editing = false; view.editDraft = null; view.editError = ''; render(); return; }
 if (action === 'confirm-correction') return saveCorrection();
 if (action === 'close-dialog') return closeDialog();
 if (action === 'photo-prev' && view.photo) return showPhoto(view.photo.index - 1);
 if (action === 'photo-next' && view.photo) return showPhoto(view.photo.index + 1);
 if (action === 'photo-retry' && view.photo) { const {id,index} = view.photo,key = `${id}:${index}`; view.photoErrors.delete(key); view.photoAttempts[key] = (view.photoAttempts[key] || 0) + 1; showPhoto(index); return; }
 if (action === 'select-sender') {
 const epoch = sessionEpoch, profile = control.dataset.profile;
 view.sendersLoading = true; render();
 try { const data = demo ? {selected_profile:profile} : await jsonRequest('/api/safar/senders/select',{method:'POST',body:JSON.stringify({profile_id:profile,chat_id:view.chatId})}); if (epoch !== sessionEpoch || !view.senders) return; view.senders.selected_profile = data.selected_profile || profile; for (const p of view.senders.profiles) p.selected = p.id === view.senders.selected_profile; toast(t('senderSelected')); }
 catch (error) { if (epoch === sessionEpoch) toast(error.message); }
 finally { if (epoch === sessionEpoch) { view.sendersLoading = false; render(); } } return;
 }
 if (action === 'logout') return logout();
 if (action === 'pair-start') return startPairing();
 if (action === 'pair-check') { clearTimeout(pairingTimer); return checkPairing(); }
 if (action === 'pair-cancel') { pairSequence++; clearTimeout(pairingTimer); view.pair = null; view.pairBusy = false; view.pairError = ''; render(); return; }
 if (action === 'install' && view.installPrompt) { const prompt = view.installPrompt; view.installPrompt = null; await prompt.prompt(); render(); }
});
document.addEventListener('submit',event => { if (event.target.id === 'correctionForm') { event.preventDefault(); reviewCorrection(); } else if (event.target.id === 'returnForm') { event.preventDefault(); createReturnCase(); } else if (event.target.id === 'intakeForm') { event.preventDefault(); submitIntake(); } else if (event.target.id === 'easyReturnLinkForm') { event.preventDefault(); submitEasyReturnLink(); } else if (event.target.id === 'warehouseReceiptForm') { event.preventDefault(); submitWarehouseReceipt(); } else if (event.target.id === 'returnExpenseForm') { event.preventDefault(); submitReturnExpense(); } else if (event.target.id === 'archiveOrderForm') { event.preventDefault(); submitArchiveOrder(); } });
document.addEventListener('input',event => {
 if (event.target.id === 'orderSearch') { view.search = event.target.value; clearTimeout(searchTimer); searchTimer = setTimeout(() => { view.orders = []; fetchOrders(); },300); }
 if (event.target.closest('#correctionForm')) { if (view.editDraft) view.editDraft[event.target.name] = event.target.value; }
});
document.addEventListener('change',async event => {
 if (event.target.id === 'senderFilter') view.sender = event.target.value;
 else if (event.target.id === 'dateFilter') view.period = event.target.value;
 else if (event.target.id === 'sortFilter') view.sort = event.target.value;
 else if (event.target.id === 'scopeFilter') { sessionEpoch++; view.chatId = Number(event.target.value); view.orders = []; view.detailCache = {}; view.tracking = {}; view.carrierEvents = {}; view.radarErrors = {}; view.senders = null; view.analytics = null; view.returnCases = []; view.returnTracking = {}; view.returnTrackingErrors = {}; view.selected = null; view.lastSync = null; await Promise.allSettled([fetchOrders(),fetchSenders(),fetchAnalytics()]); return; }
 else return;
 view.orders = []; await fetchOrders();
});
document.addEventListener('error',event => {
 const image = event.target;
 if (!(image instanceof HTMLImageElement) || !image.dataset.photoKey) return;
 view.photoErrors.add(image.dataset.photoKey);
 const parent = image.parentElement;
 image.remove();
 if (parent) { const fallback = document.createElement('div'); fallback.className = 'photo-fallback'; fallback.innerHTML = icon('photo') + `<span>${esc(t('photoError'))}</span>`; parent.prepend(fallback); }
},true);
document.addEventListener('keydown',event => { if (!view.photo) return; if (event.key === 'ArrowRight') { event.preventDefault(); showPhoto(view.photo.index + 1); } if (event.key === 'ArrowLeft') { event.preventDefault(); showPhoto(view.photo.index - 1); } });
for (const dialog of document.querySelectorAll('dialog')) { dialog.addEventListener('cancel',event => { event.preventDefault(); closeDialog(); }); dialog.addEventListener('click',event => { if (event.target === dialog) { const r = dialog.getBoundingClientRect(); if (event.clientX < r.left || event.clientX > r.right || event.clientY < r.top || event.clientY > r.bottom) closeDialog(); } }); }
window.addEventListener('offline',() => { view.offline = true; render(); });
window.addEventListener('online',() => { view.offline = false; render(); if (view.authed) refreshCurrent(); else if (!demo && !view.pair) boot(); if (view.pair) schedulePairing(); });
window.addEventListener('beforeinstallprompt',event => { event.preventDefault(); view.installPrompt = event; if (view.tab === 'settings') render(); });
window.addEventListener('appinstalled',() => { view.installPrompt = null; toast(t('installed')); });
document.addEventListener('visibilitychange',() => { if (document.hidden) clearTimeout(pairingTimer); else { if (view.authed && Number(view.expiresAt) * 1000 <= Date.now()) clearSession(t('expired')); if (view.pair) schedulePairing(); } });
window.addEventListener('pageshow',async event => { if (event.persisted && !demo) { clearSession(); view.loading = true; render(); await boot(); } });
if ('serviceWorker' in navigator && (location.protocol === 'https:' || ['localhost','127.0.0.1'].includes(location.hostname))) { navigator.serviceWorker.register('/safar/sw.js',{scope:'/safar'}).catch(() => {}); }
render(); boot();
})();
