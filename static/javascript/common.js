
    function numberToWords(num, over20=true) {
        if (num === 0) return "zero";
        if (num > 20 && over20) return num;

        const ones = ["", "one", "two", "three", "four", "five", "six", "seven", "eight", "nine", "ten", "eleven", "twelve", "thirteen", "fourteen", "fifteen", "sixteen", "seventeen", "eighteen", "nineteen"];
        const tens = ["", "", "twenty", "thirty", "forty", "fifty", "sixty", "seventy", "eighty", "ninety"];
        const scales = ["", "thousand", "million", "billion"];

        function convertLessThanOneThousand(n) {
            let str = "";

            if (n >= 100) {
                str += ones[Math.floor(n / 100)] + " hundred ";
                n %= 100;
            }

            if (n >= 20) {
                str += tens[Math.floor(n / 10)] + " ";
                n %= 10;
            }

            if (n > 0) {
                str += ones[n] + " ";
            }

            return str.trim();
        }

        let wordResult = "";
        let scaleIndex = 0;

        // Handle negative numbers
        if (num < 0) {
            return "minus " + numberToWords(Math.abs(num));
        }

        while (num > 0) {
            let chunk = num % 1000;
            if (chunk !== 0) {
                let chunkStr = convertLessThanOneThousand(chunk);
                wordResult = chunkStr + (scales[scaleIndex] ? " " + scales[scaleIndex] : "") + " " + wordResult;
            }
            num = Math.floor(num / 1000);
            scaleIndex++;
        }

        return wordResult.trim();
    }
    function numberToWords(num, over20=true) {
        if (num === 0) return "zero";
        if (num > 20 && over20) return num;

        const ones = ["", "one", "two", "three", "four", "five", "six", "seven", "eight", "nine", "ten", "eleven", "twelve", "thirteen", "fourteen", "fifteen", "sixteen", "seventeen", "eighteen", "nineteen"];
        const tens = ["", "", "twenty", "thirty", "forty", "fifty", "sixty", "seventy", "eighty", "ninety"];
        const scales = ["", "thousand", "million", "billion"];

        function convertLessThanOneThousand(n) {
            let str = "";

            if (n >= 100) {
                str += ones[Math.floor(n / 100)] + " hundred ";
                n %= 100;
            }

            if (n >= 20) {
                str += tens[Math.floor(n / 10)] + " ";
                n %= 10;
            }

            if (n > 0) {
                str += ones[n] + " ";
            }

            return str.trim();
        }

        let wordResult = "";
        let scaleIndex = 0;

        // Handle negative numbers
        if (num < 0) {
            return "minus " + numberToWords(Math.abs(num));
        }

        while (num > 0) {
            let chunk = num % 1000;
            if (chunk !== 0) {
                let chunkStr = convertLessThanOneThousand(chunk);
                wordResult = chunkStr + (scales[scaleIndex] ? " " + scales[scaleIndex] : "") + " " + wordResult;
            }
            num = Math.floor(num / 1000);
            scaleIndex++;
        }

        return wordResult.trim();
    }
    
function capitalizeFirstLetter(str) {
    if (!str) return ""; // Prevents errors on empty strings
    return str.charAt(0).toUpperCase() + str.slice(1);
}