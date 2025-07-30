a=int(input())
b=str(a).replace('-','')
c=str(b)[::-1]
if int(c)==a:
    print('true')
else:
    print('false')