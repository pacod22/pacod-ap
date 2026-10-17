import type { LocalizedDoc, Locale, ContactFormLabels } from '$lib/legal/types';
import { COMPANY, legalName, address, businessHours, eikLabel } from '$lib/legal/company';

const email = COMPANY.email;
const tel = COMPANY.phone.replace(/\s/g, ''); // tel: href needs no spaces

export const contactForm: Record<Locale, ContactFormLabels> = {
	en: {
		heading: 'Send us a message',
		nameLabel: 'Your name',
		namePlaceholder: 'Ivan Petrov',
		emailLabel: 'Email address',
		emailPlaceholder: 'you@email.com',
		messageLabel: 'Message',
		messagePlaceholder: 'How can we help?',
		submit: 'Send message',
		hint: `Submitting opens your email app with the message pre-filled. Prefer email? Write to <a href="mailto:${email}">${email}</a> directly.`,
		subjectPrefix: 'Website enquiry from',
		visitorFallback: 'a visitor'
	},
	bg: {
		heading: 'Изпратете ни съобщение',
		nameLabel: 'Вашето име',
		namePlaceholder: 'Иван Петров',
		emailLabel: 'Имейл адрес',
		emailPlaceholder: 'you@email.com',
		messageLabel: 'Съобщение',
		messagePlaceholder: 'С какво можем да помогнем?',
		submit: 'Изпрати съобщението',
		hint: `Изпращането отваря Вашето приложение за имейл с предварително попълнено съобщение. Предпочитате имейл? Пишете директно на <a href="mailto:${email}">${email}</a>.`,
		subjectPrefix: 'Запитване от уебсайта от',
		visitorFallback: 'посетител'
	},
	de: {
		heading: 'Senden Sie uns eine Nachricht',
		nameLabel: 'Ihr Name',
		namePlaceholder: 'Erika Mustermann',
		emailLabel: 'E-Mail-Adresse',
		emailPlaceholder: 'sie@email.com',
		messageLabel: 'Nachricht',
		messagePlaceholder: 'Wie können wir helfen?',
		submit: 'Nachricht senden',
		hint: `Beim Absenden öffnet sich Ihr E-Mail-Programm mit der vorausgefüllten Nachricht. Lieber per E-Mail? Schreiben Sie direkt an <a href="mailto:${email}">${email}</a>.`,
		subjectPrefix: 'Anfrage über die Website von',
		visitorFallback: 'einem Besucher'
	},
	ru: {
		heading: 'Отправьте нам сообщение',
		nameLabel: 'Ваше имя',
		namePlaceholder: 'Иван Петров',
		emailLabel: 'Адрес эл. почты',
		emailPlaceholder: 'you@email.com',
		messageLabel: 'Сообщение',
		messagePlaceholder: 'Чем мы можем помочь?',
		submit: 'Отправить сообщение',
		hint: `При отправке откроется Ваше почтовое приложение с уже заполненным сообщением. Предпочитаете электронную почту? Пишите напрямую на <a href="mailto:${email}">${email}</a>.`,
		subjectPrefix: 'Запрос с сайта от',
		visitorFallback: 'посетителя'
	}
};

/** Address cell shown in the "How to reach us" table (entity + address + ЕИК). */
function addressCell(l: Locale): string {
	return `${legalName[l]}<br>${address[l]}<br>${eikLabel[l]} ${COMPANY.eik}`;
}

export const contact: LocalizedDoc = {
	en: {
		eyebrow: 'Contact',
		title: 'Get in touch',
		lead: 'Have a question, a partnership idea, or feedback? We read every message and aim to reply within two business days.',
		metaDescription: 'Get in touch with the PACOD team.',
		sections: [
			{
				heading: 'How to reach us',
				blocks: [
					{
						k: 'table',
						kind: 'keyval',
						rows: [
							['Email', `<a href="mailto:${email}">${email}</a>`],
							['Phone', `<a href="tel:${tel}">${COMPANY.phone}</a> — ${businessHours.en}`],
							['Address', addressCell('en')]
						]
					}
				]
			}
		]
	},
	bg: {
		eyebrow: 'Контакт',
		title: 'Свържете се с нас',
		lead: 'Имате въпрос, идея за партньорство или обратна връзка? Четем всяко съобщение и се стремим да отговорим в рамките на два работни дни.',
		metaDescription: 'Свържете се с екипа на PACOD.',
		sections: [
			{
				heading: 'Как да се свържете с нас',
				blocks: [
					{
						k: 'table',
						kind: 'keyval',
						rows: [
							['Имейл', `<a href="mailto:${email}">${email}</a>`],
							['Телефон', `<a href="tel:${tel}">${COMPANY.phone}</a> — ${businessHours.bg}`],
							['Адрес', addressCell('bg')]
						]
					}
				]
			}
		]
	},
	de: {
		eyebrow: 'Kontakt',
		title: 'Kontakt aufnehmen',
		lead: 'Haben Sie eine Frage, eine Idee für eine Partnerschaft oder Feedback? Wir lesen jede Nachricht und bemühen uns, innerhalb von zwei Werktagen zu antworten.',
		metaDescription: 'Nehmen Sie Kontakt mit dem PACOD-Team auf.',
		sections: [
			{
				heading: 'So erreichen Sie uns',
				blocks: [
					{
						k: 'table',
						kind: 'keyval',
						rows: [
							['E-Mail', `<a href="mailto:${email}">${email}</a>`],
							['Telefon', `<a href="tel:${tel}">${COMPANY.phone}</a> — ${businessHours.de}`],
							['Adresse', addressCell('de')]
						]
					}
				]
			}
		]
	},
	ru: {
		eyebrow: 'Контакты',
		title: 'Свяжитесь с нами',
		lead: 'Есть вопрос, идея о партнёрстве или отзыв? Мы читаем каждое сообщение и стремимся ответить в течение двух рабочих дней.',
		metaDescription: 'Свяжитесь с командой PACOD.',
		sections: [
			{
				heading: 'Как с нами связаться',
				blocks: [
					{
						k: 'table',
						kind: 'keyval',
						rows: [
							['Эл. почта', `<a href="mailto:${email}">${email}</a>`],
							['Телефон', `<a href="tel:${tel}">${COMPANY.phone}</a> — ${businessHours.ru}`],
							['Адрес', addressCell('ru')]
						]
					}
				]
			}
		]
	}
};
