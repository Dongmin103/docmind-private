import reactStringReplace from 'react-string-replace';

export const replaceText = (text: string) => {
  // Remove duplicate \n
  const nextText = text.replace(/(\n)\1+/g, '$1');

  const replacedText = reactStringReplace(
    nextText,
    /(\[ERROR\].+\s)/g,
    (match, i) => {
      return (
        <span key={i} className={'text-state-error'}>
          {match}
        </span>
      );
    },
  );

  return replacedText;
};
